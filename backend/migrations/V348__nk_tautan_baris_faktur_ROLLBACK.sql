-- Rollback V348: cabut tautan baris NK -> baris faktur (kode sesudah V348 WAJIB dibalik dulu).
DROP INDEX IF EXISTS idx_cni_original_invoice_item;
ALTER TABLE credit_note_items DROP COLUMN IF EXISTS original_invoice_item_id;
