-- Terapkan HANYA lewat /root/apply_mig.sh (psql -v ON_ERROR_STOP=1 -1 = satu transaksi); psql telanjang tak atomik.
-- V407 (DRAFT -- BELUM DITERAPKAN; menunggu putusan pemilik) -- idx_products_tenant_sku jadi PARSIAL (deleted_at IS NULL).
-- Latar: SKU barang yang sudah dihapus (soft delete) tak bisa dipakai ulang karena indeks unik lama tak memfilter
-- deleted_at; idx_products_tenant_item_code dan idx_products_tenant_nama SUDAH parsial -- sku satu-satunya yang tidak.
-- Terukur prod 11 Okt 2026: 185 baris, 52 terhapus, duplikat (tenant,sku) di antara baris AKTIF = 0.
-- Urutan: indeks baru (nama sementara) dibangun DULU, baru yang lama dibuang -> keunikan tak pernah terbuka walau
-- runner tanpa transaksi. Nama akhir SAMA dengan yang lama (items.py INDEKS_* / test_nama_barang_409 memetakan nama).
-- PENDAMPING WAJIB di kode (jendela yang sama): items.py cek duplikat SKU ("SELECT id FROM products WHERE tenant_id=$1
-- AND sku=$2") harus ditambah `AND deleted_at IS NULL` -- tanpa itu 409 tetap muncul walau indeks sudah parsial.
DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM (
        SELECT 1 FROM products WHERE sku IS NOT NULL AND deleted_at IS NULL
        GROUP BY tenant_id, sku HAVING count(*) > 1) d;
    IF n > 0 THEN
        RAISE EXCEPTION 'V407: % pasangan (tenant,sku) ganda di barang aktif -- selesaikan dulu', n;
    END IF;
END $$;

CREATE UNIQUE INDEX idx_products_tenant_sku_aktif
    ON products (tenant_id, sku) WHERE sku IS NOT NULL AND deleted_at IS NULL;
DROP INDEX idx_products_tenant_sku;
ALTER INDEX idx_products_tenant_sku_aktif RENAME TO idx_products_tenant_sku;
