# KONTRAK — Detail faktur setara detail SO (S1, PENJUALAN-SERAGAM) — 20 Okt 2026

Status: U1+U2 LIVE `21f542c2`; U3 V430 terpasang 20 Okt 06:50Z + kode di cabang `feat/ar-rincian`. Putusan MASTER 20 Okt.
Hukum: Law 1/16/29 (angka uang dari jurnal), Law 9, Law 34 (klaim dokumen = hipotesis; angka di bawah DIUKUR).

## U1 — GET /sales-invoices/{id} == baris daftar
| medan | sumber (SATU, dipakai detail & daftar) |
|---|---|
| position_text | `faktur_daftar.posisi_faktur(status, total, sisa_baris)`: Draf · Belum lunas · Sebagian · Lunas · Batal (kata == kolom Status FE r288). Sisa = jurnal (`compute_ar_outstanding`), bukan cache status. Telat BUKAN posisi (jatuh_tempo_keadaan). |
| aksi | `faktur_aksi.aksi_faktur` |
| jatuh_tempo_terbuka / _hari / _minggu_ini / _keadaan | `_syarat_terbuka_ar()` + `faktur_daftar.*` |
| documents[] | `faktur_daftar.dokumen_per_faktur` |
Daftar juga membawa position_text. Harness `scripts/ops/uji-nyata-detail-faktur.py`.

## U2 — GET /sales-invoices/{id}/documents (izin sales_invoice R)
Pembentuk per-kind GENERIK `services/dokumen_bundel` (dipakai bundel SO, faktur, dan modul BACKEND2). Kepala `anchor`
(bukan `so`). Kelompok: rekap+pnw (bila ber-SO) · pro = proforma ASAL · kw (amount = bagian faktur INI) · ki = SJ + faktur
sendiri (juga draf → "draf") · nk asal. Harness `scripts/ops/uji-nyata-dokumen-faktur.py` (SO lama == baru 654 bundel;
faktur ⊆ SO; jumlah == documents[] daftar). FE: pembaca ketat SO (`so` + rekap pertama) perlu varian `anchor`.

## U3 — payment_summary (V430 compute_ar_rincian, opsi b)
`{invoice_total, receipt_amount, dp_applied_amount, credit_note_amount, paid_amount, remaining_amount}` (float, pola SO).
Fungsi BARU; `compute_ar_outstanding` TIDAK disentuh (md5 `00be3b22…` sebelum == sesudah apply). Cabang = salinan:
receipt = cabang 1 (proporsional) + cabang 4 (lepas, negatif); dp = cabang 3; NK = cabang 2. Draf: sisa = total; void: 0.
Gerbang `scripts/gate_v430_rincian.sh` + `scripts/ops/uji-v430-rincian.py` (badan / fungsi / detail): 124/124 faktur
6 tenant outstanding sama; payment_summary == amount_paid/amount_due 71 + 101 faktur. Kontrol merah: tanpa DP → FAIL,
proporsional → FAIL, rincian None → FAIL.

**Celah tercatat (bukan pemblokir, putusan MASTER):** cabang 4 (`RECEIVE_PAYMENT_UNAPPLY`) tak punya data nyata (0 jurnal
di semua tenant 20 Okt) → gerbang BUTA untuknya; teksnya salinan persis cabang 4 `compute_ar_outstanding`. Saat lepas
pembayaran pertama terjadi, jalankan ulang `uji-v430-rincian.py fungsi`. Selama dua salinan cabang hidup, setiap perubahan
`compute_ar_outstanding` WAJIB diikuti V43x yang sama di `compute_ar_rincian` + gerbang ini.

**Tiket (bukan dibangun):** `applied_credits[]` detail memuat NK lewat `credit_note_applications`; cabang 2 hanya
`original_invoice_id` (0 aplikasi 20 Okt).
