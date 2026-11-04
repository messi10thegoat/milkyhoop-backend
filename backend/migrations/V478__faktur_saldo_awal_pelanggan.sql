-- V478 (BACKEND2 5 Nov 2026; B0 putusan pemilik "saldo awal pelanggan = FAKTUR PEMBUKA", cermin V458 vendor; migrasi butuh izin pemilik LANGSUNG;
-- terapan = jendela MASTER lewat /root/apply_mig.sh --require-marker)
-- Saldo awal piutang pelanggan dicatat sebagai FAKTUR PEMBUKA (sales_invoices) -- jurnal INVOICE Dr Piutang Usaha / Cr Modal Saldo Awal
-- lewat penulis faktur yang SAMA (_internal_post_invoice, cabang PEMBUKA: tanpa pendapatan/SJ/HPP/PPN); masuk compute_ar_rincian/outstanding
-- sebagai faktur biasa (umur, Terima pembayaran, batal = void). Menggantikan jurnal lepas source 'OPENING' yang tak masuk sub-ledger piutang.
-- Aditif: kolom bawaan false -> faktur lama tak berubah. Indeks unik parsial = paling banyak SATU faktur pembuka AKTIF per pelanggan (penjaga DB).
-- Idempoten (IF NOT EXISTS). Jalankan -v ON_ERROR_STOP=1 -1. WAJIB diterapkan SEBELUM restart kode B0 (penulis faktur membaca kolom ini).
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS is_opening_balance boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN sales_invoices.is_opening_balance IS 'V478: faktur pembuka saldo awal piutang pelanggan (Dr Piutang / Cr Modal Saldo Awal); dibuat HANYA lewat POST /customers/{id}/opening-balance';
CREATE UNIQUE INDEX IF NOT EXISTS uq_si_saldo_awal_pelanggan ON sales_invoices (tenant_id, customer_id) WHERE is_opening_balance AND status <> 'void';
