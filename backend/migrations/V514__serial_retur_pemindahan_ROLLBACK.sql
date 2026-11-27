-- Pembalik V514 (serial S3). Aman selama flag serial_v1 belum pernah dinyalakan (kolom kosong).
ALTER TABLE credit_note_items DROP COLUMN IF EXISTS serials;
ALTER TABLE vendor_credit_items DROP COLUMN IF EXISTS serials;
ALTER TABLE stock_transfer_items DROP COLUMN IF EXISTS serials;
DELETE FROM schema_migrations WHERE version = 'V514__serial_retur_pemindahan.sql';
