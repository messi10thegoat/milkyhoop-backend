-- V224: Sales Orders — bawa syarat DP dari Penawaran (Tahap 2)
--
-- MASALAH: quotes punya dp_percent, dp_amount, terms, payment_bank_name,
-- payment_account_number, payment_account_holder. sales_orders TIDAK punya
-- satu pun. Saat konversi quote->SO niat DP LENYAP tanpa peringatan, dan
-- Proforma (Tahap 3) tidak punya sumber untuk tahu berapa yang ditagih.
--
-- Aditif murni: 6 kolom nullable, NOL backfill, NOL perubahan perilaku
-- baris lama. Tidak menyentuh jurnal — konversi quote->SO tidak menjurnal.
--
-- Presisi disamakan PERSIS dengan quotes (dp_percent numeric(5,2),
-- dp_amount numeric(18,2)) supaya nilai yang disalin tidak berubah bentuk.
--
-- Catatan penamaan: kolom `terms` di quotes dipetakan ke `payment_terms`
-- di sales_orders (nama `terms` terlalu umum untuk header pesanan).

ALTER TABLE sales_orders
    ADD COLUMN IF NOT EXISTS dp_percent             numeric(5,2),
    ADD COLUMN IF NOT EXISTS dp_amount              numeric(18,2),
    ADD COLUMN IF NOT EXISTS payment_terms          text,
    ADD COLUMN IF NOT EXISTS payment_bank_name      text,
    ADD COLUMN IF NOT EXISTS payment_account_number text,
    ADD COLUMN IF NOT EXISTS payment_account_holder text;

COMMENT ON COLUMN sales_orders.dp_percent IS 'Persentase uang muka yang disepakati, disalin dari quote saat konversi. NULL = tanpa DP.';
COMMENT ON COLUMN sales_orders.dp_amount IS 'Nominal uang muka yang disepakati, disalin dari quote saat konversi. NULL = tanpa DP.';
COMMENT ON COLUMN sales_orders.payment_terms IS 'Syarat pembayaran (quotes.terms) yang dibawa ke pesanan.';
