-- V458 (BACKEND2 25 Okt 2026; aturan "Saldo awal hutang vendor = tagihan pembuka" + migrasi ini DISETUJUI LANGSUNG pemilik; terapan = jendela MASTER)
-- SALDO-AWAL-VENDOR-A: saldo awal hutang vendor dicatat sebagai TAGIHAN PEMBUKA (bills) -- jurnal Dr 3-50000 Modal Saldo Awal / Cr 2-10100
-- Hutang Usaha lewat jalur posting tagihan (source BILL), tanpa PPN/stok/GRN; masuk compute_ap sebagai tagihan biasa (umur, jatuh tempo,
-- dibayar lewat Pembayaran Keluar, batal = void). Menggantikan jurnal VENDOR_OPENING_BALANCE lama yang TERBALIK (Dr Hutang).
-- Aditif: kolom bawaan false -> tagihan lama tak berubah. Indeks unik parsial = paling banyak SATU tagihan pembuka AKTIF per vendor
-- (penjaga di DB, bukan konvensi kode). Idempoten (IF NOT EXISTS). Jalankan -v ON_ERROR_STOP=1 -1.
-- WAJIB diterapkan SEBELUM restart kode A: tagihan_aksi.blok_posting membaca bills.is_opening_balance untuk SETIAP posting tagihan.
ALTER TABLE bills ADD COLUMN IF NOT EXISTS is_opening_balance boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN bills.is_opening_balance IS 'V458: tagihan pembuka saldo awal hutang vendor (Dr Modal Saldo Awal / Cr Hutang Usaha); dibuat HANYA lewat POST /vendors/{id}/opening-balance';
CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_saldo_awal_vendor ON bills (tenant_id, vendor_id) WHERE is_opening_balance AND status_v2 <> 'void';
