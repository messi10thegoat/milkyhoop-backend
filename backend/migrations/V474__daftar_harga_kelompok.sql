-- V474: DAFTAR HARGA per kelompok pelanggan (DAFTAR-HARGA tahap A, 4 Nov 2026, BACKEND2; desain + putusan MASTER, pola Accurate
-- "Kategori Pelanggan -> Tingkat Harga" / NetSuite price level). Kelompok = price_lists (mis. WARUNG, ECERAN; satu bawaan per tenant);
-- pelanggan menunjuk SATU daftar (customers.price_list_id; NULL = daftar bawaan tenant); harga per barang x SATUAN x tingkat qty
-- (price_list_items.min_quantity, per satuan baris). Penentu harga SATU di server (services/harga_jual), bukan fungsi DB get_item_price.
-- Terukur 3 Nov: price_lists / price_list_items / customer_price_lists = 0 baris di SEMUA tenant -> perubahan indeks aman.
-- ADITIF untuk data hidup: kolom baru NULL / bawaan false, nol baris lama disentuh. customer_price_lists dibiarkan (0 baris; dihapus
-- di migrasi pembersihan nanti, putusan MASTER). Pembalik: V474__daftar_harga_kelompok_ROLLBACK.sql.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS price_list_id UUID NULL REFERENCES price_lists(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_customers_price_list ON customers (tenant_id, price_list_id) WHERE price_list_id IS NOT NULL;

-- price sudah NUMERIC(18,2) di prod (terukur gerbang-mig 4 Nov) -> tak diubah; 2dp ditegakkan schema + Decimal (Law 9/25)
ALTER TABLE price_list_items ADD COLUMN IF NOT EXISTS is_default_unit BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE price_list_items DROP CONSTRAINT IF EXISTS price_list_items_harga_cek;
ALTER TABLE price_list_items ADD CONSTRAINT price_list_items_harga_cek CHECK (price >= 0 AND min_quantity > 0);
-- UNIQUE lama bocor untuk unit NULL (NULL selalu beda) -> indeks unik dengan unit dinormalisasi (NULL/'' = satuan dasar)
ALTER TABLE price_list_items DROP CONSTRAINT IF EXISTS uq_price_list_items;
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_list_items_satuan ON price_list_items
    (price_list_id, item_id, lower(btrim(COALESCE(unit, ''))), min_quantity);

-- satu daftar BAWAAN aktif per tenant
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_lists_bawaan ON price_lists (tenant_id) WHERE is_default AND is_active;
