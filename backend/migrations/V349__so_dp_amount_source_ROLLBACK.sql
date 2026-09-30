-- Rollback V349 (kode sesudah V349 WAJIB dibalik dulu).
ALTER TABLE sales_orders DROP COLUMN IF EXISTS dp_amount_source;
