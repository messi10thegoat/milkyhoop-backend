# Recovery Closure & Golden Path Handover — 2026-07-24

> **MULAI DARI SINI** untuk melanjutkan. Dokumen ini self-contained.
> Pemulihan **BELUM SELESAI**: golden path = gate terakhir yang belum dijalankan.

---

## 1. State saat ini

| Item | Nilai |
|------|-------|
| Droplet | `ssh root@159.89.202.160` (Ubuntu 24.04, SGP1) — **IP lama 159.89.197.131 SUDAH MATI** |
| Backend | 7 container healthy, `curl http://localhost:8001/healthz` → 200 |
| DB aktif `milkydb` | **murni hasil resep**, 0 tenant, 0 jurnal |
| Resep terakhir | **178 OK / 0 FAIL / 15 SKIP**, gap_patch Gap 1–12 semua OK |
| Commit | `b86b6c06` (origin/master), main tree bersih di master |
| `milkyhoop.com` | **521** — nginx belum ada, FE belum di-deploy |

DB lain di server (boleh dihapus kalau sudah tak perlu):
`milkydb_contaminated_20260724` (state E2E-hijau lama, terkontaminasi),
`milkydb_prev_20260724`, `milkydb_dryrun`.

---

## 2. Resep fresh install (satu-satunya sumber kebenaran skema)

```
Step 0 (di dalam runner: extensions + stub Prisma)
  └─ scripts/fresh-install/run_migrations_v9.sh   # PGDB=<db> bash ...
       ├─ MIGDIR = backend/migrations/            (V002–V200)
       └─ FIXDIR = scripts/fresh-install/migration_fixes/   (26 file terkoreksi)
  └─ scripts/fresh-install/gap_patch.sh           # PGDB=<db> bash ...
       └─ Gap 1–12 (Gap 10/11/12 = assertion fail-loud, bukan tambalan)
```

Contoh build bersih (skrip helper ada di `/root/build_fresh.sh` di droplet):
```bash
docker exec milkyhoop-dev-postgres-1 psql -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS milkydb_fresh;" -c "CREATE DATABASE milkydb_fresh;"
PGDB=milkydb_fresh bash scripts/fresh-install/run_migrations_v9.sh
PGDB=milkydb_fresh bash scripts/fresh-install/gap_patch.sh
```

**PENTING:** `FIXDIR` dulu menunjuk `/tmp/migration_fixes` — 26 file yang hanya
hidup di `/tmp`, satu reboot dari hilang. Sudah diselamatkan ke repo (`4bef5c23`).

---

## 3. V195–V200: apa dan kenapa

Semua ditemukan oleh **gate fresh-install**, bukan analisis statik.
**4 dari 5 perbaikan adalah bug produksi nyata, bukan artefak recovery.**

| Migrasi | Bug | Dampak sebelum fix |
|---------|-----|--------------------|
| **V195** | `compute_journal_hash` + kolom `chain_sequence`/`content_hash`/`previous_hash` tidak pernah didefinisikan migrasi manapun (V145/V188 hanya memanggil). Plus `pgcrypto`, `pay_groups`, `user_pay_group_access`, dan 30+ kolom terpakai. | **SETIAP insert jurnal gagal** — `record "new" has no field "chain_sequence"` |
| **V196** | Enum `Role`, 14 kolom `User`, 7 kolom `Tenant`, tabel `refresh_tokens` + `pending_registrations` | **Signup mustahil** → E2E dari nol tak bisa jalan |
| **V197** | V025 bikin `seed_default_tax_codes(TEXT)`; V167 "mengganti"-nya dengan `(VARCHAR)` → jadi **overload**, bukan pengganti. Versi lama menang dan melanggar CHECK yang dipasang V167 sendiri. | **Setiap tenant baru 500** saat signup |
| **V198** | V165 menambah CoA `5-20850` + role `BANK_FEE`. V168/V173/V183 mendefinisikan ulang `seed_default_coa()` dengan menyalin versi LAMA → kontribusi V165 terhapus diam-diam. | Transfer bank ber-biaya-admin gagal post |
| **V199** | V120 ("Fix V059") memakai arity berbeda → overload, bukan pengganti. V059 (yang benar-benar jalan) hanya mengenali `account_type 'ASSET'`/`'LIABILITY'`, padahal `1-10400 Piutang Usaha = RECEIVABLE` dan `2-10100 Hutang Usaha = PAYABLE`. | **Rasio keuangan SALAH SAJI untuk semua tenant** — AR tak masuk aset lancar, AP tak masuk kewajiban lancar. `current_ratio`, `quick_ratio`, `cash_ratio`, `debt_ratio`, `debt_to_equity` |
| **V200** | — | Menutup **kelas** clobber-seed (lihat §4) |

### Bukti fungsional yang sudah dijalankan

- **Hash chain** (DB murni-resep): chain terbentuk `GENESIS → b3669b66f61a → dd6af9e72bfc`;
  `verify_chain_integrity` valid semua; lalu **satu baris jurnal diubah** →
  seq 1 `is_valid=false`, seq 2 tetap `true`. **Tamper level-baris terdeteksi.**
- **Signup HTTP nyata**: register → verify-link (302, status `verified`) →
  complete-setup 200. Provisioning: CoA **70**, account_roles **38**, tax_codes **9**.
  Nol duplikat `account_code`, nol duplikat `role_key`, nol role yatim.
- **V200 uji negatif**: clobber disimulasikan di dalam transaksi → assertion
  MELEDAK dengan daftar hilang; pasca-ROLLBACK kembali OK.

---

## 4. ATURAN YANG BERLAKU (jangan dilanggar)

1. **Arbiter skema = KODE Python DAN fungsi/migrasi SQL.**
   V195 sempat membuang `tax_codes.coretax_tax_code` karena grep
   `routers/services/schemas` nol referensi — padahal ditulis fungsi SQL
   (`V167:76` di dalam `seed_default_tax_codes`). Dikoreksi di V197.
2. **JANGAN pernah menyalin skema dari `milkydb` lama.** DB itu terkontaminasi
   tambalan ad-hoc agen E2E: fungsi hash dikarang tanpa `journal_lines`
   (tamper-evidence lumpuh), `customers.name` dilemahkan jadi NULLABLE,
   `pending_registrations` tanpa `attempt_count`, kolom fiktif nol-referensi.
3. **Redefinisi fungsi seed WAJIB berbasis `pg_get_functiondef` (definisi hidup),
   BUKAN salinan file migrasi lama.** Menyalin file lama persis penyebab V198.
4. **`CREATE OR REPLACE FUNCTION` dengan signature berbeda = OVERLOAD BARU,
   bukan pengganti.** Sumber V197 dan V199. Setelah mengubah fungsi, cek:
   ```sql
   SELECT proname, count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname='public' AND p.prokind='f' GROUP BY 1 HAVING count(*)>1;
   ```
5. **Guard wajib diuji NEGATIF.** Guard yang tak pernah dibuktikan bisa gagal =
   false-green (pelajaran V188).

---

## 5. PENDING — urutan yang disepakati

### 5.1 GOLDEN PATH (gate terakhir, PRIORITAS 1)
Jalankan di DB murni-resep, dari signup, via HTTP nyata:

1. Signup tenant baru → verifikasi seed (CoA 70, roles 38, tax 9)
2. Master data: vendor, customer, item raw + finished, BoM, work center
   (`labor_rate_per_hour` 15000, `overhead_rate_per_hour` 5000), warehouse
3. Bill + bill payment
4. Work Order: material issue → labor → overhead auto-apply
5. FG receipt → **WIP (1-10650) net 0**
6. Payroll: PPh21 + BPJS
7. Invoice PSAK-72 **3 event**: billing (Dr AR / Cr 2-10750 Deferred) →
   fulfillment (COGS + recognize) → pelunasan (Dr Bank / Cr AR)
8. Expense (biaya)
9. **Bank transfer dengan BANK_FEE** (uji V198 end-to-end)
10. Manual JV
11. Period close

**Verifikasi wajib:** 10 master invariant + **Law 4 (2-10300 = 0)** +
`verify_chain_integrity(tenant)` semua `is_valid=true` + costing identity +
`assert_seed_contract('<tenant>')`.

**Aturan:** lapor SETIAP error dengan pesan persis. Stop kalau ada kegagalan
struktural. **Jangan tambal diam-diam** — perbaikan masuk migrasi, bukan
`psql` ad-hoc. Itu persis kesalahan yang menciptakan milkydb terkontaminasi.

### 5.2 Sisanya
- **Update IP** `159.89.197.131` → `159.89.202.160` di **18 tempat**:
  `milkyhoop-env` (9), `milkyhoop-e2e` (4), `milkyhoop-pdf` (1),
  `milkyhoop-clean` (2), `milkyhoop-tutorial` (2). Sync ke Dropbox SKILL setelahnya.
- **Deploy key di droplet.** Droplet TIDAK punya kredensial push (remote HTTPS
  tanpa token, tak ada SSH key). Semua push sesi ini lewat Mac
  `/Users/antoniwan/milkyhoop-backend-recovered` (alias SSH `github-milky`):
  ```bash
  git fetch ssh://root@159.89.202.160/root/milkyhoop-dev master:refs/remotes/droplet/master -f
  git push origin refs/remotes/droplet/master:master
  ```
- **nginx + FE** — `milkyhoop.com` masih **521**. Cek dulu bagaimana FE dulu
  disajikan (container `:3001`?), lalu pasang Cloudflare Origin Cert untuk
  Full (strict). Ingat: `location = /service-worker.js` HARUS `proxy_pass`,
  bukan `try_files` (lihat gotcha #14 di `milkyhoop-env`).
- **ROTASI DB PASSWORD** — `Proyek771977` ada di git history. `docker-compose.yml`
  sudah pakai `${DB_PASSWORD}` dari `.env`, tapi nilainya belum dirotasi.
- **Follow-up V120** — `gross_margin` + `net_margin` dan `RETURNS JSON` milik
  V120 sengaja TIDAK di-port ke V199 karena mengubah kontrak keluaran ke
  frontend. Keputusan owner apakah mau ditambahkan.
- **`production_completions.is_overrun`** sudah masuk V196 (dikonfirmasi dipakai
  `production.py:2279` lewat guard `allow_overrun:2222`).

---

## 6. Backup

`~/Dropbox/MILKYHOOP/RECOVERY_2026-07-23/`

| File | Isi |
|------|-----|
| `milkyhoop-backend.git` | **mirror git**, sudah di-refresh ke `b86b6c06` (V195–V200 termasuk) |
| `milkyhoop-backend-code-20260723_161800.tar.gz` | tarball kode (28M) |
| `milkyhoop-backend-migrations-20260723_161800.tar.gz` | tarball migrations (291K) |
| `milkydb-e2egreen-20260724.sql.gz` | dump `milkydb` lama (E2E-hijau, **terkontaminasi** — referensi pembanding saja, JANGAN dijadikan sumber skema) |

Segarkan mirror setelah push berikutnya:
`git -C ~/Dropbox/MILKYHOOP/RECOVERY_2026-07-23/milkyhoop-backend.git remote update --prune`

---

## 7. Posisi jujur

Pemulihan **belum selesai** — golden path adalah gate terakhir dan belum
dijalankan. Tapi fondasinya sekarang **lebih kuat daripada sebelum droplet
hilang**: fresh install-nya **terbukti**, bukan diasumsikan. Sebelum ini tak
seorang pun pernah menjalankan resep dari DB kosong, dan itulah sebabnya lima
bug di §3 bisa hidup berdampingan tanpa terdeteksi.

---

## 8. Backlog drift skema (hasil audit sistematis 2026-07-24)

Dihasilkan `scripts/audit/audit_insert_schema_drift.py` (419 file .py vs 261
tabel). **SUDAH ditriase — jangan tambal borongan.** Yang terbukti dipakai
jalur hidup sudah masuk V202. Sisanya di bawah, BELUM diverifikasi hidup/mati.

### Sudah diperbaiki
| Tabel | Kolom | Migrasi |
|---|---|---|
| `bill_items` | tax_rate, tax_amount, dpp | V202 |
| `bill_payments_v2` | operational_status, accounting_status | V202 |
| `customers` | jaminan nama dipindah ke kolom kanonik | V201 |
| `inventory_ledger` (trigger) | 2 trigger merujuk kolom NEW fiktif | V203 |

### Belum ditriase — 26 tabel
Kemungkinan besar KODE MATI (nama terlarang Iron Laws — kanonik `journal_id` /
`journal_date`, bukan `journal_entry_id` / `posting_date`):
`journal_entries` (created_by_name, entry_date, journal_type, memo,
posting_date, updated_by → branches.py, cheques.py, payment_request_service.py) ·
`journal_lines` (description, journal_entry_id → idem)

Modul pinggir, belum tersentuh golden path:
`accounts_payable` + `accounts_receivable` (balance, created_by, total_amount,
vendor_id → opening_balance.py — **OB per-entity akan 500**) ·
`bank_transactions` (account_id, contact_id, is_credit, reference, source_id,
source_type, updated_at → bank_reconciliation.py, cheques.py) ·
`inventory_ledger` (item_id, quantity_change, total_value, transaction_date →
stock_transfers.py) · `sales_invoice_items` (item_name, line_total,
sales_invoice_id, tax_id → recurring_invoices.py) · `quotes` (5 kolom teks/bank) ·
`products` (is_active → kernel_document_executor.py) · `chat_messages` (7 kolom) ·
`exchange_rates` · `cost_pools` · `overhead_allocations` · `pending_actions` ·
modul KDS/restoran (`kds_orders`, `kds_order_items`, `kds_stations`,
`menu_categories`, `menu_items`, `recipes`, `recipe_ingredients`,
`recipe_instructions`, `restaurant_tables`, `table_areas`, `table_sessions`)

### 32 tabel yang DIRUJUK kode tapi TIDAK ADA di DB
`action_patterns, chat_attachments, chat_events, chat_telemetry, efaktur_exports,
expense_claim_lines, expense_claims, expense_policies, granular_permissions,
journal_sequences, kds_order_history, master_data_audit_log, menu_item_modifiers,
nsfp_assignments, product_djp_mapping, product_units, recipe_modifier_groups,
recipe_modifier_options, recurring_expenses, reservations,
sales_invoice_attachments, sensitive_data_access, tax_group_items, tax_groups,
tax_info, tax_invoice_items, tax_invoice_sources, tax_invoices, tool_call_logs,
user_explicit_preferences, user_preferences, user_profiles, waitlist,
withholding_tax_records`

Beberapa jelas fitur yang belum di-deploy; beberapa (mis. `user_explicit_preferences`
untuk memori bot Tier 2, `tax_invoices` untuk e-Faktur) mungkin migrasinya ikut
hilang bersama droplet. **Perlu triase terpisah, bukan asumsi.**

### UNKNOWN yang sengaja tidak dikarang
Tidak dapat dipastikan apakah trigger `update_warehouse_stock_from_ledger`
(V043) pernah berfungsi di droplet lama — mungkin ada migrasi hilang yang dulu
menambahkan `inventory_ledger.item_id`/`quantity_change`. Yang PASTI: pada resep
saat ini dia rusak, dan `product_id`/`quantity_in`/`quantity_out` terbukti
kanonik dari 3 call-site penulis. Dicatat sebagai unknown, bukan kesimpulan.

### Gap lain yang tercatat saat golden path
- `work_centers` punya tabel + dipakai BoM, tapi **NOL endpoint API** (tidak di
  `main.py` maupun `production.py`). Dibuat via SQL sebagai fixture E2E.
- Skill `milkyhoop-e2e` masih menunjuk dunia lama: IP `159.89.197.131`,
  base URL `https://milkyhoop.com/api` (masih 521), kredensial
  `grapmanado@gmail.com` / tenant `grapgrap` (hilang bersama droplet).

---

## 9. Golden path progress (2026-07-24) + backlog UPDATE-drift

### Migrasi/fix dari golden path DB murni-resep
| # | Bug | Perbaikan |
|---|---|---|
| V201 | POST /customers 500 (name NOT NULL vestige) | pindah jaminan ke kolom kanonik nama |
| V202 | bill_items pajak + bill_payments_v2 dual-status | tambah kolom (tiru sibling AR) |
| V203 | trigger inventory rujuk kolom NEW fiktif | perbaiki 2 trigger (product_id/quantity_in/out) |
| V204 | pengeluaran stok ditolak chk_ws_quantity | trigger UPDATE-dulu-baru-INSERT |
| round2 (kode) | payroll calculate 500 int.quantize | round2 coerce via d() |
| V205 | void invoice 500 (revenue_status not_applicable) | perlebar CHECK (simetris fulfillment) |
| V206 | pelunasan penjualan 500 (bank_transaction_id) | tambah kolom (tiru bill_payments_v2) |

### Langkah golden path yang HIJAU (tenant konveksi-cemerlang, fresh)
1 signup+seed (CoA 71, roles 38, BANK_FEE ok) · 2 master data · 3 pembelian
+PPN Masukan +pelunasan (5-artifact) · 4 manufaktur WIP net 0 NON-trivial
(FG WAC 71.500 benar) · 5 payroll multi-line (BPJS EE+ER, PPh21=0 sah) ·
6 PSAK-72 3-event +PPN Keluaran +pelunasan +VOID-CASCADE.
BELUM: 7 expense+bank transfer(BANK_FEE)+JV · 8 period close · invariant akhir.

### Backlog UPDATE-drift (audit_update_schema_drift.py, 2026-07-24) — TRIASE, jangan tambal borongan
Semua PERIPHERAL / kemungkinan kode mati, NOL di jalur core:
- journal_entries.posted_at/posted_by (cheques.py:219) — core posting TIDAK pakai
- bills.paid_amount (vendor_deposits.py) · branch_transfers/menu_categories/
  table_areas.updated_at · chat_sessions.final_summary/status · credit_notes/
  vendor_credits.tax_invoice_id · customers.points/total_nilai/total_transaksi/
  last_transaction_at (members.py) · products.content_unit/stock_quantity
  (transactions.py, opening_balance.py) · reconciliation_sessions.* · table_sessions.*

---

## 10. GOLDEN PATH SELESAI — HIJAU PENUH (2026-07-24)

Tenant `konveksi-cemerlang`, DB murni-resep, HTTP nyata. 8 langkah signup->period close.

### 7 migrasi + 1 fix kode dari golden path (semua sudah di origin/master)
| # | Bug (500 di jalur nyata) | Kelas |
|---|---|---|
| V201 | POST /customers — name NOT NULL vestige V024 | constraint pada kolom mati |
| V202 | bill_items pajak + bill_payments_v2 dual-status | drift kolom (INSERT) |
| V203 | trigger inventory rujuk NEW.item_id/quantity_change fiktif | trigger V043 vs skema |
| V204 | pengeluaran stok ditolak chk_ws_quantity | pola upsert (logika, bukan kolom) |
| round2 (kode Python) | payroll calculate int.quantize | sum([])=int |
| V205 | void invoice — revenue_status not_applicable | CHECK asimetris sibling |
| V206 | pelunasan penjualan — receive_payments.bank_transaction_id | drift kolom (UPDATE) |

### 15 invariant final — SEMUA LULUS
TB seimbang (147.443.200) · 0 jurnal tak-seimbang · 0 orphan · Law-4 (2-10300)=0 ·
WIP net 0 · Deferred net 0 · applied labor/OH=0 · AR GL==helper (0) · AP GL==helper (0) ·
verify_chain_integrity 26/26 valid · bank==GL · inventory GL==warehouse_stock×WAC
(2.555.000) · PPN GL-derived · **Neraca seimbang** (Aset 50.103.000 = Kew 10.229.100
+ Ekuitas 39.873.900) · P&L residual pasca-closing 0. is_effective 18/26.

### Angka kunci yang benar (bukti non-trivial)
- FG WAC 71.500/pcs = bahan 67.500 + konversi 4.000 (WIP net 0 NON-trivial)
- PPN Masukan 495.000 (11%×4.5jt) · PPN Keluaran 280.500 (11%×2.55jt)
- BANK_FEE 5-20850 = 6.500 pada bank transfer nyata (V198 terbukti end-to-end)
- Payroll multi-line: BPJS EE 360k + ER 948.6k, PPh21=0 sah (di bawah PTKP)
- Void-cascade: 3 reversal, stok pulih, 4 akun net 0, original tetap POSTED

### Catatan (bukan bug — untuk owner)
- Selisih Produksi 8.8jt di closing = unabsorbed labor variance yang JUJUR:
  bayar gaji sebulan (9jt) tapi hanya 1 WO 10-jam menyerap 200k. Reconcile
  mereklasifikasi 100% 5-20100 ke produksi (desain agresif). Di bulan nyata,
  banyak WO menyerap labor itu.
- JV liar 1.000 (uji lock pra-close) bocor ke periode; artefak, seimbang.
- Sales invoice header tax_rate TIDAK dipropagate ke item (beda dari bill V2
  yang header-driven); PPN penjualan HARUS di-set per-item. Footgun, bukan blocker.
- work_centers: tabel + dipakai BoM tapi endpoint create ada di /api/bom/work-centers
  (bukan /api/production) — KOREKSI catatan §8 sebelumnya yang bilang "nol endpoint".

### Status DoD
[x] Golden path hijau + 15 invariant  [x] V201-V206 + round2 commit & push
[x] Rebuild bersih terakhir: 184 OK / 0 FAIL / 15 SKIP dari DB kosong, V201-V206 semua OK, Gap 10/11/12 OK
[ ] Jalur B: nginx + FE + Origin Cert (milkyhoop.com masih 521)

---

## 11. KEPUTUSAN DIPERLUKAN SEBELUM CUSTOMER (jangan terkubur di backlog)

### D1 — Reconcile mereklasifikasi 100% Beban Gaji ke produksi (kelas V199: salah-diam)
`POST /production/month-end-reconcile` menyapu SELURUH 5-20100 Beban Gaji
sebagai actual production labor, lalu selisih vs applied-labor -> 5-90200
Selisih Produksi. Di skenario uji (semua karyawan = produksi) itu BENAR.

**Di konveksi nyata SALAH:** ada gaji admin, sales, owner yang BUKAN biaya
produksi. Konsekuensi: gaji non-produksi ikut tersapu ke Selisih Produksi ->
**OpEx understated, variance produksi overstated.** Buku tetap seimbang,
angka tetap rapi -> tidak melapor dirinya sendiri (persis kelas V199 rasio
salah-saji).

Bukti dari golden path: payroll 9jt (2 operator), applied labor 200k,
Selisih Produksi 8,8jt. Kalau salah satu dari dua itu admin, 8,8jt itu memuat
gaji admin yang seharusnya OpEx.

**Opsi desain (keputusan owner, BUKAN agen):**
- (a) flag `employees.is_production` -> reconcile hanya sapu labor karyawan produksi
- (b) akun terpisah: 5-20100 Gaji Produksi vs 5-20110 Gaji Admin/Umum;
  payroll posting route by employee type; reconcile hanya sentuh 5-20100
- (c) alokasi berbasis jam kerja aktual ke WO

Rekomendasi teknis: (b) paling bersih + auditable + sejajar pola CoA existing,
tapi butuh payroll posting sadar-tipe-karyawan. Owner putuskan.

### D2 — Rotasi DB_PASSWORD (Proyek771977 ada di git history). Sudah ${DB_PASSWORD} di compose, nilai belum dirotasi.

### D3 — Lapis 3: UI E2E (belum pernah tersentuh). Backend terbukti; drive A-Z dari browser setelah web hidup.

---

## 12. JALUR B — milkyhoop.com HIDUP (2026-07-24)

Arsitektur web dipulihkan:


Yang dikerjakan:
- CF Origin Cert (*.milkyhoop.com, valid s/d 2041) di /etc/ssl/cloudflare/
  (key chmod 600). Pasangan cert/key diverifikasi cocok (modulus hash).
- Host nginx 1.24 dipasang: config /etc/nginx/sites-available/milkyhoop.conf,
  real-IP Cloudflare, 80->443 redirect, /api proxy 300s (chat/LLM), SW no-cache.
- Frontend container di- (image sudah ter-build 144MB, build React 23-Jul).
- nginx enabled + container restart=always (survive reboot).

VERIFIKASI:
- Via Cloudflare: HTTP 200, <title>MilkyHoop, bundle main.6a02fcc0.js.
- 80->443: 301. /api via CF: gateway tercapai. 521 HILANG.
- Signup register lewat domain: {"success":true} — rantai CF->nginx->gateway->DB
  bekerja end-to-end lewat URL publik.

TERSISA (follow-up):
- **Deploy key**: keypair /root/.ssh/id_ed25519_deploy dibuat + remote 
  (git@github-deploy:...) diset. PUBLIC KEY perlu ditambahkan owner ke GitHub
  repo Settings -> Deploy keys (Allow write). Sesudah itu droplet push langsung
  tanpa lewat Mac.
- **DB milkydb masih berisi data golden-path** (tenant konveksi-cemerlang +
  konveksi-bintang-timur + e2e-*). Bukan "production bersih". Kalau mau mulai
  bersih untuk customer: /milkyhoop-clean atau rebuild fresh.
- **Frontend container nginx.conf punya X-Frame-Options: DENY** -> akan blokir
  PDF-preview iframe (blob:) milik app sendiri (gotcha #12). Belum di-fix; tak
  blokir login/signup. Perlu longgarkan ke frame-src blob: saat PDF dipakai.
- **IP skill 159.89.197.131 -> 159.89.202.160**: SUDAH diupdate (26 kemunculan,
  7 file) + sync Dropbox.

### KEPUTUSAN yang masih menunggu owner (dari §11)
D1 reconcile-basis (kelas V199) · D2 rotasi DB_PASSWORD · D3 Lapis 3 UI E2E.

---

## 13. CHATMODE dipulihkan (2026-07-24) + isu API key

Chatmode MATI TOTAL di DB murni-resep (skema chat tak lengkap). Diperbaiki:
| Migrasi | Fix |
|---|---|
| V207 | chat_messages stub Prisma lama -> skema baru (session_id/role/content/…); chat_sessions.status |
| V208 | chat_events (tabel absen; log_event sinkron, tak try/except -> crash tool paths) |
| V209 | pending_actions.is_direct; intent_decision_log partisi DEFAULT (partisi bulan berjalan hilang) |

Verifikasi HTTP nyata (milkyhoop.com, tenant konveksi-cemerlang): chitchat,
query pipeline (Gemini polish), agent-loop, CREATE (propose card) — semua jalan.

### API key (bukan bug skema — untuk owner)
- **OPENAI_API_KEY lama MATI (401 Incorrect API key).** Diganti key baru owner
  (valid, tested gpt-4o-mini 200). Dipakai: agent-loop, vision OCR, chitchat
  greeting, title gen.
- **GOOGLE_API_KEY (Gemini) ditambah** (var yang dibaca kode = GOOGLE_API_KEY).
  Works, TAPI **FREE TIER limit 20 req/hari** -> 429 saat pemakaian berat.
  Dipakai: extraction, query polish, chitchat. **Owner perlu setup billing
  Gemini** (AI Studio: Set up prepay) utk pemakaian produksi; sementara
  circuit-breaker fallback ke OpenAI menutup sebagian.
- Kedua key di /root/milkyhoop-dev/.env (bukan git, ter-gitignore). Rotasi
  DB_PASSWORD (D2) masih terpisah.

### Sisa non-fatal (tak blokir chat, telemetri/UI)
chat_telemetry (fire-and-forget), user_profiles + users/tenants.logo_url
(profil/UI), tool_call_logs. Tercatat di backlog §8/§9 (tabel absen).

