-- V402 (10 Okt 2026, MASTER GO B1+B2): indeks unik mengikuti soft-delete.
-- B1: nama barang. Indeks lama (tenant_id, nama_produk) TANPA filter deleted_at -> nama milik barang yang SUDAH
--     DIHAPUS tetap terkunci; cek app hanya melihat yang aktif -> INSERT/UPDATE 500 galat DB mentah (terukur di kaos).
--     Kini parsial WHERE deleted_at IS NULL, sejajar idx_products_tenant_item_code -> nama boleh dipakai ulang sesudah
--     dihapus; jalur INSERT lain (impor, matrix, intake) ikut tertolong. Prasyarat diukur: 0 duplikat nama aktif.
-- B2: pelanggan. uq_customers_tenant_name (CONSTRAINT) & idx_customers_tenant_code_unique ada di kolom lama name/code
--     yang 100% NULL di semua tenant (app menulis nama/nomor_member) -> tak menjaga apa pun; dihapus. Nama pelanggan
--     TANPA indeks unik (cek app menolak nama aktif persis sama; "mirip" = peringatan FE). Nomor pelanggan dijaga DB:
--     unik parsial (deleted_at IS NULL AND nomor_member IS NOT NULL). Prasyarat diukur: 0 duplikat.
DROP INDEX IF EXISTS idx_products_tenant_nama;
CREATE UNIQUE INDEX idx_products_tenant_nama ON products (tenant_id, nama_produk) WHERE deleted_at IS NULL;

ALTER TABLE customers DROP CONSTRAINT IF EXISTS uq_customers_tenant_name;
DROP INDEX IF EXISTS idx_customers_tenant_code_unique;
CREATE UNIQUE INDEX uq_customers_tenant_nomor_member ON customers (tenant_id, nomor_member)
    WHERE deleted_at IS NULL AND nomor_member IS NOT NULL;
