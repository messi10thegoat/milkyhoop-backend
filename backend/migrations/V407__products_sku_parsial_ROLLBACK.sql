-- Jalankan dengan psql -v ON_ERROR_STOP=1 -1 (satu transaksi). Tanpa itu, guard yang gagal TIDAK menghentikan DROP INDEX
-- dan indeks unik hilang tanpa pengganti (terukur di scratch 11 Okt).
-- Rollback V407: kembalikan idx_products_tenant_sku ke definisi V072 (unik atas semua baris ber-SKU, termasuk terhapus).
-- GAGAL BERISIK bila sejak V407 ada SKU yang dipakai ulang (baris aktif + baris terhapus ber-SKU sama): indeks lama
-- tak bisa dibangun; ubah/kosongkan SKU baris terhapus itu dulu (jangan hapus barisnya -- dokumen merujuknya).
DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM (
        SELECT 1 FROM products WHERE sku IS NOT NULL GROUP BY tenant_id, sku HAVING count(*) > 1) d;
    IF n > 0 THEN
        RAISE EXCEPTION 'Rollback V407: % pasangan (tenant,sku) ganda bila terhapus ikut dihitung -- bereskan dulu', n;
    END IF;
END $$;

CREATE UNIQUE INDEX idx_products_tenant_sku_lama ON products (tenant_id, sku) WHERE sku IS NOT NULL;
DROP INDEX idx_products_tenant_sku;
ALTER INDEX idx_products_tenant_sku_lama RENAME TO idx_products_tenant_sku;
