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
