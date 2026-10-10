-- ROLLBACK V402. Gagal bila sementara itu ada nama barang dipakai ulang (aktif + terhapus) -- itu memang tujuan V402;
-- selesaikan duplikatnya dulu.
DROP INDEX IF EXISTS uq_customers_tenant_nomor_member;
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_tenant_code_unique ON customers (tenant_id, code) WHERE code IS NOT NULL;
ALTER TABLE customers ADD CONSTRAINT uq_customers_tenant_name UNIQUE (tenant_id, name);

DROP INDEX IF EXISTS idx_products_tenant_nama;
CREATE UNIQUE INDEX idx_products_tenant_nama ON products (tenant_id, nama_produk);
