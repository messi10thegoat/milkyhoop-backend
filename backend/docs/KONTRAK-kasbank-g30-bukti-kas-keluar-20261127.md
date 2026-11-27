# KONTRAK BE — Kas&Bank G30: Bukti Kas Keluar (PDF) Uang Keluar manual (27 Nov 2026)

`GET /api/bank-transactions/{transaction_id}/pdf` — izin = `kas_bank` R (sama dengan daftar/detail). Baca saja, tanpa migrasi, tanpa flag.

- 200: `application/pdf` (A5 melintang, templat kwitansi dengan label keluar), `Content-Disposition` nama = nomor transaksi, `Cache-Control: no-store`.
- 404 `{"detail":"Mutasi manual tidak ditemukan."}` — id tak ada / tenant lain / bukan mutasi manual sah.
- 409 `{"detail":"Bukti Kas Keluar hanya untuk Uang Keluar."}` — mutasi itu Uang Masuk.
- Mutasi dibatalkan → tetap tercetak dengan tanda DIBATALKAN (+ tanggal & alasan void).

Isi dokumen (angka TERSIMPAN dari jurnal sisi rekening, nilai mutlak): No. Transaksi, tanggal, Dibayarkan kepada (pihak), Jumlah Dikeluarkan + terbilang, rekening, "Akun lawan: <kode> · <nama>", keterangan, kolom tanda tangan "Dikeluarkan oleh,".

Contoh (FE: buka sebagai blob/tab baru, bukan JSON): `GET /api/bank-transactions/6f1c…/pdf` → `%PDF-…`;
galat contoh: `{"detail":"Bukti Kas Keluar hanya untuk Uang Keluar."}` (409).
