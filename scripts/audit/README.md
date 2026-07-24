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
