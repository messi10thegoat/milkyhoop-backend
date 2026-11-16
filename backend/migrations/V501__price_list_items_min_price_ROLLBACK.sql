-- ROLLBACK V501: buang harga minimum per baris. Data min_price HILANG; kembalikan KODE BUG-040 (min_price di tulis/resolve/impor) SEBELUM menjalankan berkas ini.
ALTER TABLE price_list_items DROP CONSTRAINT IF EXISTS chk_price_list_items_min_price_nonneg;
ALTER TABLE price_list_items DROP COLUMN IF EXISTS min_price;
