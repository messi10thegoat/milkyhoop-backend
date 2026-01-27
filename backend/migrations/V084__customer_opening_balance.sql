-- ============================================================================
-- V084: Customer Opening Balance Support
-- ============================================================================
-- Add fields to customers table for tracking opening balances during
-- data migration or initial setup.
--
-- Fields:
-- - ar_opening_balance: Initial AR balance
-- - deposit_opening_balance: Initial deposit balance
-- - opening_balance_date: Cutoff date for opening balance
-- - opening_balance_notes: Notes about the opening balance setup
-- ============================================================================

-- Add opening balance fields to customers table
ALTER TABLE customers
ADD COLUMN IF NOT EXISTS ar_opening_balance BIGINT DEFAULT 0,
ADD COLUMN IF NOT EXISTS deposit_opening_balance BIGINT DEFAULT 0,
ADD COLUMN IF NOT EXISTS opening_balance_date DATE,
ADD COLUMN IF NOT EXISTS opening_balance_notes TEXT;

-- Add index for customers with opening balances
CREATE INDEX IF NOT EXISTS idx_customers_opening_balance
ON customers (tenant_id, opening_balance_date)
WHERE opening_balance_date IS NOT NULL;

-- Comment on columns
COMMENT ON COLUMN customers.ar_opening_balance IS 'Opening AR balance from migration/initial setup (in IDR)';
COMMENT ON COLUMN customers.deposit_opening_balance IS 'Opening deposit balance from migration/initial setup (in IDR)';
COMMENT ON COLUMN customers.opening_balance_date IS 'Cutoff date for opening balance entry';
COMMENT ON COLUMN customers.opening_balance_notes IS 'Notes about opening balance setup or migration source';
