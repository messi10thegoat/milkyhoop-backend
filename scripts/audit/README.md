# scripts/audit

Alat audit skema-vs-kode. Dipakai saat golden path 2026-07-24; menemukan
drift yang tidak terlihat oleh analisis manual.

## audit_insert_schema_drift.py

Mengekstrak SETIAP daftar kolom `INSERT INTO <tabel> (...)` dari seluruh
file `.py` backend, lalu men-diff-nya terhadap `information_schema` DB hidup.

```bash
python3 scripts/audit/audit_insert_schema_drift.py
```

Keluaran: per tabel, kolom yang ditulis kode tapi TIDAK ADA di skema, lengkap
dengan `file:baris` penulisnya.

### Cara membaca hasilnya

**JANGAN tambal borongan.** Hasilnya WAJIB ditriase — sebagian besar temuan
adalah kode mati. Contoh nyata: penulis `journal_lines.journal_entry_id` dan
`journal_entries.posting_date` memakai nama yang justru DILARANG Iron Laws
(kanonik: `journal_id`, `journal_date`); itu jalur legacy, bukan drift.

Triase yang benar:
1. Cari SIAPA memanggil call-site itu. Jalur hidup atau modul mati?
2. Cek apakah ada PEMBACA kolom tersebut (Python DAN fungsi/view SQL).
3. Kalau ada kembaran (mis. `bill_items` vs `sales_invoice_items`,
   `bill_payments_v2` vs `receive_payments`), tiru definisi kembarannya.
4. Baru tulis migrasi.

### Batasan yang diketahui

Pola hanya menangkap `INSERT INTO tbl (kolom...) VALUES|SELECT` dengan daftar
kolom bebas tanda kurung. Versi awal alat ini memakai pola longgar dan
menghasilkan ARTEFAK (mis. `bill_of_materials` seolah kehilangan 24 kolom,
padahal itu `INSERT .. SELECT` yang membuat pola menelan span lintas-statement).
Kalau menambah pola, uji ulang terhadap kasus itu.

Tidak menangkap: INSERT dinamis (nama tabel/kolom dari variabel), ORM,
dan UPDATE.

## audit_stock_trigger_functional.sql

Uji FUNGSIONAL (bukan statik) trigger stok inventory
(update_warehouse_stock_from_ledger + update_batch_stock_from_ledger).

```bash
docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d milkydb < scripts/audit/audit_stock_trigger_functional.sql
```

### Kenapa ada — dan kenapa statik tak cukup

audit_insert_schema_drift.py membandingkan NAMA kolom. Ia LOLOSKAN bug V204:
trigger memakai kolom yang benar (product_id/quantity_in/quantity_out) tapi
POLA UPSERT-nya salah — INSERT..ON CONFLICT dengan delta negatif ditolak
chk_ws_quantity SEBELUM ON CONFLICT mengalihkan ke UPDATE, sehingga SETIAP
pengeluaran stok gagal. Hanya EKSEKUSI NYATA 2-arah yang menangkap ini.

Ini instance KETIGA kelas "trigger V043-era rusak sejak fresh install":
V195 (kolom hilang), V203 (kolom NEW fiktif), V204 (pola upsert). Dua yang
pertama ketahuan grep; V204 tidak. Karenanya: setiap trigger di tabel inti
WAJIB diuji fungsional dua arah, bukan diinspeksi.

### Yang diuji (semua dalam transaksi + ROLLBACK, tidak mengubah data)
1. inbound +10 pada baris ada -> stok naik
2. outbound -30 pada baris ada -> stok turun (REGRESI V204 bila gagal)
3. over-issue melebihi stok -> DITOLAK check_violation (Law 13 anti-oversell)
4. item nol-stok keluar -> DITOLAK (tak bisa keluarkan stok yg belum masuk)
5. item nol-stok masuk -> baris baru terbentuk

Keluaran 6 baris: 5 PASS + ringkasan. Bila ada FAIL -> RAISE EXCEPTION
(self-verifying, exit non-zero). Aman di DB manapun dgn >=1 baris
warehouse_stock qty>=50.

