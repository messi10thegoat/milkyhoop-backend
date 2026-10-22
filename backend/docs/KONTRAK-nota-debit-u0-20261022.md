# KONTRAK — Nota Debit (vendor credits) unit (3) — RANCANGAN, belum dibangun (22 Okt 2026)

Sesi BACKEND, atas permintaan MASTER (PEMBELIAN-SERAGAM). Skill ironlaws dibaca; Law disebut per butir. Dasar ukur: master `adb0d84c`.
**Data: `vendor_credits` / `_items` / `_applications` / `_refunds` = 0 baris di SEMUA tenant** → nol backfill, tapi SEMUA jalur tulis belum
pernah berjalan (2 di antaranya pasti 500 hari ini). Kembaran yang ditiru = Nota Kredit Penjualan (`credit_notes.py`, `compute_ar_outstanding`
cabang 2 + `cn_unapplied`, fence V250 `trg_credit_note_kaitan_faktur_beku`) — pola NetSuite: *Vendor Credit applied to Bill = tanpa dampak GL*.

## 0. Temuan ukur yang menentukan rancangan

| # | fakta (diukur) | akibat |
|---|---|---|
| F1 | Terapkan nota debit (VC:1380) menulis `vendor_credit_applications` TANPA `journal_id`; `compute_ap_outstanding` cabang `vc_applied_debits` JOIN `vca.journal_id` | aplikasi tak pernah mengurangi sisa tagihan |
| F2 | `vc_unapplied` hanya `source_type='VENDOR_CREDIT'`; jurnal `VENDOR_CREDIT_REFUND` tak dihitung | nota debit yang dikembalikan tunai tetap tampil sebagai kredit vendor |
| F3 | `get_bill_remaining_from_journal` (VC:119-168) + 2 salinan di bill_payments.py mengurangkan SELURUH debit AP nota debit ke tiap tagihan bertaut | pagar bayar/terapkan memakai angka yang salah ke arah lain |
| F4 | Role `PURCHASE_DISCOUNT` **SUDAH ADA** (role_resolver V156) dan terpetakan di 6/6 tenant ke **5-10200 Diskon Pembelian** (COGS, kontra); dipakai potongan tunai bayar tagihan | **tak perlu role/akun/migrasi baru** untuk potongan |
| F5 | Posting: semua alasan → Cr COGS_PURCHASE_RETURN (5-10300) subtotal + keluar stok barang terlacak (dicatat 'SALE') | potongan harga mengeluarkan stok |
| F6 | Kolom hantu: PATCH menulis `vendor_credit_date` (kolom = `credit_date`); `vendor_credits.tax_invoice_id` dibaca (VC:1866, tax_invoices.py:282/568/987) tapi TAK ADA | 500 saat dipakai (Law: perbaiki KODE, bukan tambah kolom) |
| F7 | Refund hanya `bank_account_id` → `UUID(body.account_id)` None → TypeError 500 | refund lewat bank pasti gagal |
| F8 | Void: jurnal VENDOR_CREDIT dibalik benar (Law 2), TAPI keluar-stok, jurnal VENDOR_CREDIT_COGS, baris DTL tidak dibalik | Law 31 gerbang 5 dilanggar |
| F9 | Tanpa idempotensi (create/post/apply/refund/void); hanya advisory lock | klik ganda refund = 2 refund + 2 jurnal (Law 13 ≠ Law 14) |
| F10 | Tagihan asal dibaca tanpa filter tenant saat create (VC:607-611); vendor_id tak diperiksa tenant | Law 24 |
| F11 (Penjualan, di luar berkasku) | `compute_ar_outstanding.cn_unapplied` juga tak menghitung `CREDIT_NOTE_REFUND`: kaos CN-2610-0005 (1.000, dikembalikan penuh) masih tampil −1.000 untuk pelanggan "TES E2E CW-SO Pelanggan" | tiket ke MASTER (GL benar; piutang per pelanggan kurang 1.000) |

## 1. Akibat jurnal per aksi (rancangan)

| aksi | jurnal | lapisan turunan | pagar |
|---|---|---|---|
| **Buat (draf)** | — | baris; `original_bill_id` boleh diisi (tagihan milik tenant + vendor sama) | idempotensi buat (Law 14); filter tenant (Law 24) |
| **Posting — retur/rusak/lainnya** | `VENDOR_CREDIT`: Dr AP_TRADE total · Cr COGS_PURCHASE_RETURN harga jual · Cr VAT_INPUT PPN (PKP) | barang terlacak: `record_inventory_outbound` + `VENDOR_CREDIT_COGS` (seperti hari ini); DTL | Law 4 Σ baris==header; Law 5; Law 13 kunci `VENDOR_CREDIT_POST:{id}`; Law 27 peran |
| **Posting — potongan (`discount`/`pricing_error`)** | `VENDOR_CREDIT`: Dr AP_TRADE total · **Cr PURCHASE_DISCOUNT (5-10200)** harga jual · Cr VAT_INPUT PPN (PKP) | **TANPA gerak stok**; wajib berbaris nominal (`VC_BARIS_WAJIB`); bila bertaut tagihan: **blok bila > sisa jurnal tagihan** (`compute_ap_rincian`) | simetris NK potongan (putusan pemilik 21 Okt: 4-10200 + blok) |
| **Terapkan ke tagihan** | **TANPA jurnal** (hutang sudah didebit saat posting; terapkan = ATRIBUSI) | `vendor_credit_applications` aktif + `original_bill_id := tagihan` (fence DB kembar V250) · cache tagihan disegarkan dari jurnal | seluruh nilai ke SATU tagihan (kembar NK); tagihan aktif (bukan draf/void) + vendor sama + muat sisa jurnal; kunci `VENDOR_CREDIT_APPLY:{id}`; idempotensi |
| **Lepas penerapan** | TANPA jurnal | aplikasi → `reversed`, `original_bill_id := NULL` di transaksi yang sama (fence) | hanya bila belum dikembalikan |
| **Kembalikan dana (refund)** | `VENDOR_CREDIT_REFUND`: Dr Kas/Bank (akun TERSELESAIKAN dari bank_account_id/account_id; wajib akun kas) · Cr AP_TRADE | baris refund + `bank_transactions` (banksync Rule 1) | hanya nota debit BEBAS (tak bertaut); ≤ sisa jurnal nota debit; Law 5 tanggal refund; idempotensi |
| **Batalkan refund** | jurnal cermin (Law 2/26) | bank_txn cermin; baris refund `voided` | — |
| **Void nota debit** | cermin `VENDOR_CREDIT` (asli tetap POSTED + `reversed_by_id`) **+ cermin `VENDOR_CREDIT_COGS`** | **pembalikan keluar-stok** (`record_inventory_reversal`), DTL di-void | ditolak bila masih diterapkan / ada refund aktif (lepas/batalkan dulu); Law 31 gerbang 5 |

## 2. Sumber uang (Law 1/16/29) — perubahan fungsi AP (MIGRASI, izin pemilik)

`compute_ap_outstanding` + `compute_ap_rincian` (V441, satu migrasi, gerbang kembar V440):
- cabang nota debit per tagihan = **kembar AR cabang 2**: debit AP jurnal `VENDOR_CREDIT` (efektif, bukan pembalik) dari nota debit dengan
  `original_bill_id = tagihan` (menggantikan JOIN `vca.journal_id` yang tak pernah terisi).
- `vc_unapplied` = nota debit `original_bill_id IS NULL`: Σ debit AP `VENDOR_CREDIT` **−** Σ kredit AP `VENDOR_CREDIT_REFUND` (F2).
- Gerbang: sebelum/sesudah pada SEMUA tenant hari ini **identik** (0 baris VC → cabang kosong), md5 lama vs baru tercatat; kontrol merah
  pakai nota debit sintetis di transaksi ROLLBACK (kaos) untuk: bertaut, bebas, bebas+refund, void.
- `get_bill_remaining_from_journal` (VC) + 2 salinan di bill_payments.py → diganti `compute_ap_rincian` (satu sumber; tagihan = sisa jurnal).
- Sisa nota debit (`sql_sisa_nd`, kembar `sql_sisa_nk`): bebas = net AP jurnal efektif bersumber nota debit (VENDOR_CREDIT debit − REFUND kredit);
  bertaut/draf/void = 0. Cache `amount_applied`/`amount_received` tetap (trigger), bukan sumber baca.

## 3. Perbaikan tanpa putusan (kode saja)

F6 (kolom hantu → kode memakai `credit_date`; rujukan `tax_invoice_id` dibuang/diganti tautan yang ADA — diukur dulu di tax_invoices.py),
F7 (refund memakai akun terselesaikan + cek akun kas), F8 (void lengkap), F9 (idempotensi pola `idem_buat`/`mulai_aksi` credit_notes),
F10 (filter tenant + vendor milik tenant), teks galat Indonesia berkode `ND_*`, status tagihan saat terapkan (bukan draf/void),
response_model diperluas.

## 4. Unit & urutan usul

- **U3a — kebenaran uang** (butuh izin pemilik: model terapkan-tanpa-jurnal + V441): migrasi V441 dulu (migrasi-saja, pola V440), lalu kode
  terapkan/lepas/refund/void + satu sumber sisa + F6/F7/F10. Harness nyata kaos ROLLBACK seluruh siklus (buat→posting→terapkan→lepas→refund→
  batal refund→void) + sabotase; gerbang V441 di semua tenant.
- **U3b — aturan potongan** (izin pemilik: potongan → 5-10200 PURCHASE_DISCOUNT, tanpa stok, blok > sisa tagihan, baris wajib; PKP bruto =
  kembar `nk_potongan_pajak` atas komposisi pajak TAGIHAN) + idempotensi (F9) + pratinjau `_rencana_nota_debit`.
- **U3c — daftar/detail seragam + bundel** (sesudahnya; jenis bundel `vendor_credit`/`debit_note` disepakati dgn BACKEND2 K3; rute
  `/vendor-credits/{id}/documents` tak bentrok).

## 5. Putusan yang dibutuhkan (pemilik LANGSUNG)

1. Terapkan nota debit = atribusi tanpa jurnal, seluruh nilai ke SATU tagihan (kembar Nota Kredit; NetSuite) + ubah fungsi AP (V441).
2. Potongan (`discount`/`pricing_error`) → akun peran **PURCHASE_DISCOUNT (5-10200 Diskon Pembelian, sudah ada)**, tanpa gerak stok, blok bila
   melebihi sisa tagihan; retur/rusak/lainnya tetap 5-10300 + stok.
