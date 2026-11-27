-- Pembalik V513 (serial S1). Aman selama flag serial_v1 belum pernah dinyalakan (taut kosong).
ALTER TABLE stock_adjustment_items DROP COLUMN IF EXISTS serials;
ALTER TABLE item_serials DROP CONSTRAINT IF EXISTS chk_item_serials_status;
DROP TABLE IF EXISTS inventory_ledger_serials;
DELETE FROM schema_migrations WHERE version = 'V513__serial_taut_ledger.sql';
