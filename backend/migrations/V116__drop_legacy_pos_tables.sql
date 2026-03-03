-- ============================================================
-- V116: Drop Legacy POS Tables (B5 Migration)
-- transaksi_harian + item_transaksi → Journal-Based Architecture
-- ============================================================
-- SAFE: Both tables verified empty (0 rows each)
-- All reads migrated to inventory_ledger + journal_entries
-- All writes migrated to journal-based /sales endpoint
-- ============================================================

BEGIN;

-- Drop item_transaksi first (references transaksi_harian)
DROP TABLE IF EXISTS item_transaksi CASCADE;

-- Drop hpp_breakdown (references transaksi_harian)
DROP TABLE IF EXISTS hpp_breakdown CASCADE;

-- Drop outbox entries referencing transactions (if FK exists)
-- Note: outbox may have FK to transaksi_harian — CASCADE handles it

-- Drop the main legacy table
DROP TABLE IF EXISTS transaksi_harian CASCADE;

COMMIT;

-- Migration complete: Legacy POS tables removed
-- POS flow now uses: journal_entries + journal_lines + inventory_ledger
