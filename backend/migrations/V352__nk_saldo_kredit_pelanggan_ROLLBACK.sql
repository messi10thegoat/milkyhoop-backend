-- Rollback V352 (kode sesudah V352 WAJIB dibalik dulu).
ALTER TABLE credit_notes DROP COLUMN IF EXISTS created_deposit_id;
