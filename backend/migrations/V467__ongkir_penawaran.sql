-- V467: ONGKOS KIRIM di Penawaran (31 Okt 2026; paket Milkybook v2, putusan pemilik "ongkir kolom baru tanpa PPN").
-- Pola SAMA dengan V290 (SO/faktur): ongkir = baris sendiri di header, kode pajak ongkir EKSPLISIT
-- (NULL = ikut kode pajak barang, UU PPN Pasal 1 angka 18; "tanpa PPN" = kode NONE tenant), tarif/DPP/PPN ongkir
-- yang dipakai. Konversi Penawaran -> SO/faktur meneruskan ongkir + pilihan kode apa adanya.
-- ADITIF & idempoten: default 0/NULL -> penawaran lama TIDAK berubah angka. Nol jurnal (Penawaran tak berjurnal).
-- Pembalik: V467__ongkir_penawaran_ROLLBACK.sql (gagal keras bila ada penawaran berongkir).
ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS shipping_amount numeric(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_tax_code_id uuid REFERENCES tax_codes(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS shipping_tax_rate numeric(5,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_tax_amount numeric(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shipping_dpp numeric(18,2) NOT NULL DEFAULT 0;

ALTER TABLE quotes DROP CONSTRAINT IF EXISTS chk_quotes_shipping_nonneg;
ALTER TABLE quotes ADD CONSTRAINT chk_quotes_shipping_nonneg CHECK (shipping_amount >= 0 AND shipping_tax_amount >= 0);
