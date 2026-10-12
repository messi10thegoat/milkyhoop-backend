-- Rollback V412. Jalankan dengan -v ON_ERROR_STOP=1 -1. Menolak bila sudah ada pemindahan completed/voided (fakta stok).
-- Catatan: fungsi ship_/receive_stock_transfer TIDAK dipulihkan: keduanya menulis kolom hantu dan tak pernah bisa jalan.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM stock_transfers WHERE status IN ('completed', 'voided')) THEN
        RAISE EXCEPTION 'ROLLBACK V412 ditolak: ada pemindahan completed/voided';
    END IF;
END $$;
DROP INDEX IF EXISTS idx_stock_transfers_so;
ALTER TABLE stock_transfers DROP COLUMN IF EXISTS sales_order_id;
ALTER TABLE stock_transfers DROP COLUMN IF EXISTS voided_at;
ALTER TABLE stock_transfers DROP COLUMN IF EXISTS voided_by;
ALTER TABLE stock_transfers DROP COLUMN IF EXISTS void_reason;
ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS chk_st_status;
ALTER TABLE stock_transfers ADD CONSTRAINT chk_st_status
    CHECK (status::text = ANY (ARRAY['draft','in_transit','received','cancelled']));
