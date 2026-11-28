-- V517: DIMENSI/BERAT + MINIMAL KUANTITAS beli/jual per transaksi pada barang (ikut Accurate; GO pemilik LANGSUNG 28 Nov ±12:15 di sesi BACKEND 3).
-- ADITIF: kolom NULL = kosong; data lama TIDAK diubah, tanpa backfill. Dipakai hanya bila flag gelap dimensi_barang_v1 NYALA.
-- min_qty_* dalam SATUAN DASAR barang; hanya PERINGATAN di pratinjau dokumen (bukan blok).
-- Pembalik: V517__dimensi_minqty_barang_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig).
ALTER TABLE products ADD COLUMN IF NOT EXISTS weight NUMERIC(14,3);
ALTER TABLE products ADD COLUMN IF NOT EXISTS weight_unit TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS length NUMERIC(14,3);
ALTER TABLE products ADD COLUMN IF NOT EXISTS width NUMERIC(14,3);
ALTER TABLE products ADD COLUMN IF NOT EXISTS height NUMERIC(14,3);
ALTER TABLE products ADD COLUMN IF NOT EXISTS dimension_unit TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS min_qty_jual NUMERIC(18,4);
ALTER TABLE products ADD COLUMN IF NOT EXISTS min_qty_beli NUMERIC(18,4);
ALTER TABLE products ADD CONSTRAINT chk_products_dimensi_v517 CHECK (
    (weight IS NULL OR weight >= 0) AND (length IS NULL OR length >= 0) AND (width IS NULL OR width >= 0) AND (height IS NULL OR height >= 0)
    AND (min_qty_jual IS NULL OR min_qty_jual > 0) AND (min_qty_beli IS NULL OR min_qty_beli > 0)
    AND (weight_unit IS NULL OR weight_unit IN ('g', 'kg')) AND (dimension_unit IS NULL OR dimension_unit IN ('cm', 'm'))) NOT VALID;
