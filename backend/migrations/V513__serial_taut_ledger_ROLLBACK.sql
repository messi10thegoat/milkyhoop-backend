-- Pembalik V513 (serial S1). Aman selama flag serial_v1 belum pernah dinyalakan (taut kosong).
ALTER TABLE stock_adjustment_items DROP COLUMN IF EXISTS serials;
ALTER TABLE item_serials DROP CONSTRAINT IF EXISTS chk_item_serials_status;
ALTER TABLE item_serials ADD CONSTRAINT chk_is_status
    CHECK (status IN ('available', 'reserved', 'sold', 'returned', 'damaged', 'scrapped'));   -- gagal bila status baru sudah dipakai (flag pernah nyala)
DROP TABLE IF EXISTS inventory_ledger_serials;
DELETE FROM schema_migrations WHERE version = 'V513__serial_taut_ledger.sql';
