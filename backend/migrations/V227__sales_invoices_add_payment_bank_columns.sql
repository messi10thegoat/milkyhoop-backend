-- V227: sales_invoices — rekening tujuan cetak (tiga kolom teks), cermin quotes/sales_orders.
--
-- Tiket MASTER: "rekening tujuan faktur". Field ini HANYA instruksi cetak
-- "bayar ke mana". NOL dampak jurnal: saat faktur terbit belum ada uang, jurnal
-- tetap piutang. Akun kas/bank untuk pencatatan uang masuk tetap dipilih di
-- Penerimaan Pembayaran dan BOLEH berbeda dari yang tercetak.
--
-- BENTUKNYA SENGAJA TEKS, BUKAN FK. Terukur 2026-09-03: `quotes` (V219),
-- `sales_orders` (V224) dan `proformas` (V225) semuanya memakai tiga kolom
-- text nullable dengan nama yang sama, dan NOL FK ke bank_accounts. Sensus FK:
-- 13 constraint menunjuk bank_accounts, semuanya modul UANG (bank_transactions,
-- bill_payments_v2, vendor_deposits, cheques, ...) — nol dari dokumen penjualan.
-- Teks = snapshot cetak yang BEKU: faktur historis tidak berubah bila rekening
-- diedit atau dinonaktifkan belakangan. Itu perilaku yang benar untuk dokumen.
--
-- Panjang mengikuti batas skema quote (100/50/100) yang ditegakkan di lapisan
-- Pydantic, bukan di DB — sama seperti quotes/sales_orders yang juga `text`.
-- Idempoten (IF NOT EXISTS). Nol data migration. Nol trigger disentuh.

ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS payment_bank_name       text;
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS payment_account_number  text;
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS payment_account_holder  text;
