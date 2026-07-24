# Migration Recovery Log — 2026-07-23

**Konteks:** Droplet DO permanently deleted. Schema DB hilang (V078–V188 tidak pernah di-push ke GitHub sebelumnya). Ditemukan bahwa `milkyhoop-backend` GitHub repo branch `master` menyimpan 194 migration files V002–V188. Recovery dilakukan dari sana.

---

## File DIBUANG (tidak dieksekusi)

| File | Alasan |
|------|--------|
| `V030__credit_notes.sql` | Kolom nama salah (`code`, `type`, `tenant_id::uuid` pada VARCHAR). Digantikan penuh oleh `V030__credit_notes_fixed.sql` (superset + koreksi schema) |
| `V031__vendor_credits.sql` | Sama — kolom nama salah. Digantikan `V031__vendor_credits_fixed.sql` |
| `V032__opening_balance.sql` | Sama — kolom nama salah. Digantikan `V032__opening_balance_fixed.sql` |
| `V033__inventory_ledger_cogs.sql` | FK ke tabel nonexistent, cast `tenant_id::uuid` pada kolom VARCHAR. DDL salah. TAPI kolom COGS (unit_cost, total_cost, is_inventory_item, cogs_journal_id, cost_source, total_cogs, cogs_posted_at) yang ada di file ini dipindah ke V078 baru |
| `V033__inventory_ledger_cogs_fixed.sql` | Versi koreksi DDL (superset minus kolom COGS). Digabung: DDL-nya rename jadi V033__inventory_ledger_cogs_CORRECTED.sql, kolom COGS jadi V078 |
| `V085__receive_payments.sql` | `customer_id UUID` — salah, harusnya VARCHAR(255). Digantikan V087__receive_payments.sql (sudah ada, versi benar) |
| `V137__rollback.sql` | Rollback script eksplisit (`DROP TABLE IF EXISTS CASCADE`), header bilang "Do NOT execute unless instructed" |
| `V145__rollback.sql` | Idem — rollback script, bukan migrasi forward |
| `V145__backfill_grapgrap_walk_rebuild.sql` | Murni DML, scope tenant grapgrap saja (312 rows hash rebuild). Data dummy, tidak relevan untuk droplet baru |

---

## File DIRENUMBER

| File Asal | File Baru | Alasan |
|-----------|-----------|--------|
| `V005__add_mfa_fields.sql` | `V009__add_mfa_fields.sql` | V005 bentrok dengan `V005__add_browser_id_single_session.sql`. Slot V009 kosong dan sebelum perujuk pertama V073 |
| `V085__receive_payments.sql` | **DIBUANG** (lihat atas) | Supersede oleh V087 |
| `V120__coa_receivable_payable.sql` | `V140__coa_receivable_payable.sql` | V120 bentrok dengan `V120__arap_integrity.sql`. Slot V140 kosong dan sebelum perujuk V168 |
| `V177__deposit_application_reversal.sql` | `V192__deposit_application_reversal.sql` | V177 bentrok dengan `V177__backfill_orphan_journal_number_sequences.sql`. Tidak ada perujuk, aman di atas V188 |
| `V177__backfill_orphan_journal_number_sequences.sql` | `V193__backfill_orphan_journal_number_sequences.sql` | Companion V176 (self-healing journal number). Dipindah ke V193, tetap jalan setelah V176 |
| `V078__create_unit_conversions_item_pricing.sql` | `V194__create_unit_conversions_item_pricing.sql` | Slot V078 diambil untuk kolom COGS (wajib sebelum V115). Tidak ada forward ref ke unit_conversions sebelum V115, aman dipindah |

---

## File BARU DIBUAT

| File | Isi | Alasan |
|------|-----|--------|
| `V078__sales_invoice_cogs_columns.sql` | ADD COLUMN IF NOT EXISTS untuk: `sales_invoice_items.(unit_cost, total_cost, is_inventory_item, cost_source)` + `sales_invoices.(cogs_journal_id, total_cogs, cogs_posted_at)` | Kolom-kolom ini ada di V033 asli (yang dibuang) tapi tidak di V033_fixed. V115 ALTER TYPE pada kolom ini → wajib ada sebelum V115. Tipe BIGINT/BOOLEAN/VARCHAR/UUID/TIMESTAMPTZ sesuai V033 asli, V115 akan ALTER ke numeric(18,2) |

---

## Status Recovery (2026-07-23)

- [x] 171 OK / 0 FAIL / 16 SKIP di milkydb (PG14)
- [x] gap_patch bersih — 8 gap termasuk NextAuth tables
- [x] Backend healthy: api_gateway + ragcrud + chatbot + minio + redis
- [x] Prisma connected, PolicyEngine initialized
- [ ] E2E golden path

---

## Bug Scripts (sudah diperbaiki)

| Bug | Fix |
|-----|-----|
| `run_migrations_v9.sh` hardcode `PGDB="milkydb_dryrun"` | `${PGDB:-milkydb_dryrun}` |
| `gap_patch.sh` hardcode `DB="milkydb_dryrun"` | `${PGDB:-milkydb_dryrun}` |
| docker-compose hardcode `Proyek771977` di 3 tempat | `${DB_PASSWORD}` |
| `MINIO_PUBLIC_ENDPOINT` IP lama `159.89.197.131` | `159.89.202.160` |
| `Account`/`Session`/`VerificationToken` tidak dibuat Step 0 | Tambah Gap 8 di gap_patch |

---

## ⚠️ SECURITY — WAJIB ROTASI SEBELUM PRODUCTION

**`DB_PASSWORD=Proyek771977`** ada di git history (docker-compose.yml line 366, 1339, 1376 sebelum fix).
Siapapun yang punya akses ke repo GitHub dapat membaca password ini.

**Sebelum production:**
1. Generate password baru yang URL-safe (tanpa `/`, `@`, `+`): `openssl rand -hex 24`
2. `ALTER USER postgres PASSWORD 'new_password';`
3. Update `.env` → `DB_PASSWORD=new_password`
4. Update docker-compose agar tidak ada hardcode lagi (sudah `${DB_PASSWORD}` sekarang)
5. Restart semua containers

---

## Keputusan Pending

- V002–V009: RESOLVED — di-skip karena tabel dibuat Step 0 (Prisma layer)
- NextAuth tables (Account/Session/VerificationToken): dibuat via Gap 8

---

## RALAT 2026-07-24 — asal-usul objek yang hilang

Commit `6135a76f` menulis hipotesis bahwa objek yang hilang berasal dari
"migrasi pasca-V194 di droplet lama yang tak pernah ter-push". **Hipotesis itu
SALAH dan dengan ini dicabut.**

Bukti (transkrip sesi E2E 2026-07-23 + diff struktural DB-fresh vs milkydb):
sebagian besar objek tersebut **dibuat ad-hoc oleh agen E2E** saat menambal
error, bukan sisa migrasi. Yang benar-benar hilang dari repo hanya:
`compute_journal_hash` + 3 kolom hash-chain `journal_entries`.

### Kontaminasi milkydb (jangan dijadikan referensi)

1. **`compute_journal_hash` dikarang ulang dengan tangan.** Versi yang
   terpasang hanya menghash `tenant_id|journal_date|total_debit|prev_hash` —
   **tanpa `journal_lines`**, tanpa `total_credit`/`journal_number`/
   `source_type`. Akibatnya baris jurnal bisa diubah tanpa mengubah hash:
   tamper-evidence lumpuh. Diganti definisi kanonik baru di V195.
2. **`customers.name` dilemahkan** dari NOT NULL jadi NULLABLE.
3. Kolom fiktif nol-referensi: `employees.basic_salary`/`salary_type`,
   `sales_invoice_items.serial_no`/`uom`/`conversion_rate`/`warehouse_id`.

**Aturan yang berlaku sejak sekarang: arbiter skema = KODE (+ schema.prisma
untuk lapisan auth), BUKAN isi milkydb.**

### Status resep per 2026-07-24

- `173 OK / 0 FAIL / 15 SKIP` + gap_patch bersih, dari DB kosong.
- V090 dicabut dari SKIP setelah FIXDIR dipatch (UNIQUE berekspresi ->
  unique INDEX). Mengembalikan `warehouse_bins`, `bin_stock`, kolom products.
- V007 tetap SKIP, kini beralasan HASIL UJI: `ERROR column it.satuan does not
  exist`. Konsekuensinya `products.base_unit` (188 referensi kode) dibuat di
  V195, bukan lewat V007.
- Blok `SKIP_REASON` kedua dipindah ke scope atas. Sebelumnya berada di dalam
  body `if` milik `run_migration()` — hanya bekerja karena kebetulan V006
  ter-skip lebih dulu.
- **FIXDIR diselamatkan ke repo** (26 file, sebelumnya hanya di `/tmp`).
- Hash chain diuji fungsional di DB fresh: chain terbentuk, verify valid,
  dan perubahan satu baris jurnal TERDETEKSI (is_valid=false).

### Sisa gap yang BELUM ditutup

Lapisan Prisma/auth: `User` (14 kolom), `Tenant` (7), tabel `refresh_tokens`,
`pending_registrations`. Arbiter = `backend/api_gateway/libs/milkyhoop_prisma/
schema.prisma`. **Ini memblokir E2E dari nol karena signup butuh semuanya.**

