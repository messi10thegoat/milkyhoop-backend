-- V501: BUG-040 (16 Nov 2026, DISETUJUI LANGSUNG pemilik 00:55 WIB via MASTER) -- harga MINIMUM per baris daftar harga (paritas Accurate "Harga Minimum").
-- Kolom nullable (null = tak ada lantai), tanpa backfill, tanpa default: nol efek pada baris eksisting. CHECK hanya nonnegatif; aturan min_price <= price dijaga
-- KODE (tambah/ubah/impor) bukan CHECK, supaya ubah massal D3 yang menurunkan harga di bawah minimum menjadi PERINGATAN, bukan galat DB.
-- Pembalik: V501__price_list_items_min_price_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig membungkus).
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS min_price NUMERIC(18,2);
ALTER TABLE price_list_items DROP CONSTRAINT IF EXISTS chk_price_list_items_min_price_nonneg;
ALTER TABLE price_list_items ADD CONSTRAINT chk_price_list_items_min_price_nonneg CHECK (min_price IS NULL OR min_price >= 0);
