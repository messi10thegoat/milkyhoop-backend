-- V502: BUG-260 (16 Nov 2026) -- kolom MEREK barang (setara Accurate; kebutuhan toko grosir: saring/kelompokkan per merek). products.merek teks bebas, NULLABLE,
-- tanpa default dan TANPA backfill (semua barang lama = NULL = "tanpa merek"). Dibaca/ditulis: POST/PATCH /api/items, GET /api/items?merek=, GET /api/items/merek
-- (daftar nilai), Impor/Ekspor Barang. Indeks parsial (tenant_id, merek) untuk saringan + daftar nilai.
-- DIIZINKAN LANGSUNG pemilik (16 Nov 00:55 WIB, via MASTER): migrasi ADITIF. Pembalik: V502__products_merek_ROLLBACK.sql. Tanpa BEGIN/COMMIT.
ALTER TABLE products ADD COLUMN IF NOT EXISTS merek TEXT;
COMMENT ON COLUMN products.merek IS 'Merek barang (teks bebas, dipangkas; NULL = tanpa merek). Saringan ?merek= dan daftar nilai /api/items/merek.';
CREATE INDEX IF NOT EXISTS idx_products_tenant_merek ON products (tenant_id, merek) WHERE merek IS NOT NULL AND deleted_at IS NULL;
