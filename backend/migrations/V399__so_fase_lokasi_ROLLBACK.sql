-- Rollback V399: lepas fase lokasi (data fase HILANG; hanya data operasional, bukan keuangan).
DROP INDEX IF EXISTS idx_so_fase_lokasi;
ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS fk_so_fase_gudang;
ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS chk_so_fase_lengkap;
ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS chk_so_fase_lokasi;
ALTER TABLE sales_orders DROP COLUMN IF EXISTS fase_at, DROP COLUMN IF EXISTS fase_gudang_id, DROP COLUMN IF EXISTS fase_lokasi;
ALTER TABLE warehouses DROP CONSTRAINT IF EXISTS uq_warehouses_id_tenant;
