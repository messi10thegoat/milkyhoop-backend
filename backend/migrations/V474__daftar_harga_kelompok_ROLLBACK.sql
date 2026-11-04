-- V474 ROLLBACK: cabut daftar harga per kelompok. Penetapan daftar ke pelanggan (customers.price_list_id) IKUT HILANG -- jalankan hanya
-- bila kode yang memakainya (services/harga_jual, PATCH pelanggan price_list_id) sudah dikembalikan.
DROP INDEX IF EXISTS uq_price_lists_bawaan;
DROP INDEX IF EXISTS uq_price_list_items_satuan;
ALTER TABLE price_list_items ADD CONSTRAINT uq_price_list_items UNIQUE (price_list_id, item_id, unit, min_quantity);
ALTER TABLE price_list_items DROP CONSTRAINT IF EXISTS price_list_items_harga_cek;
ALTER TABLE price_list_items DROP COLUMN IF EXISTS is_default_unit;
DROP INDEX IF EXISTS idx_customers_price_list;
ALTER TABLE customers DROP COLUMN IF EXISTS price_list_id;
