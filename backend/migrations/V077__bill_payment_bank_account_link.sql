-- ============================================================================
-- V077: Link Bill Payments to Bank Accounts
-- ============================================================================
-- Purpose: Add bank_account_id support for bill payments to enable:
--          - Frontend using bank account selector instead of raw CoA picker
--          - Automatic bank transaction creation on payment
--          - Sign convention handling for credit cards
-- ============================================================================

-- ============================================================================
-- 1. ADD COLUMNS TO BILL_PAYMENTS
-- ============================================================================

-- Add bank_account_id (optional - backward compatible)
ALTER TABLE bill_payments
ADD COLUMN IF NOT EXISTS bank_account_id UUID REFERENCES bank_accounts(id);

-- Add bank_transaction_id (link to created bank transaction)
ALTER TABLE bill_payments
ADD COLUMN IF NOT EXISTS bank_transaction_id UUID REFERENCES bank_transactions(id);

-- Add tenant_id for RLS (if not exists)
ALTER TABLE bill_payments
ADD COLUMN IF NOT EXISTS tenant_id TEXT;

-- Populate tenant_id from parent bill
UPDATE bill_payments bp
SET tenant_id = b.tenant_id
FROM bills b
WHERE bp.bill_id = b.id
AND bp.tenant_id IS NULL;

-- ============================================================================
-- 2. ADD INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_bill_payments_bank_account
    ON bill_payments(bank_account_id) WHERE bank_account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bill_payments_bank_transaction
    ON bill_payments(bank_transaction_id) WHERE bank_transaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bill_payments_tenant
    ON bill_payments(tenant_id);

-- ============================================================================
-- 3. ADD COMMENTS
-- ============================================================================

COMMENT ON COLUMN bill_payments.bank_account_id IS
    'Optional link to bank_accounts. When provided, coa_id is derived from bank account and bank_transaction is created.';

COMMENT ON COLUMN bill_payments.bank_transaction_id IS
    'Link to bank_transactions record created for this payment. NULL if using legacy account_id flow.';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V077: Bill Payment Bank Account Link completed';
    RAISE NOTICE '- Added bank_account_id column (optional)';
    RAISE NOTICE '- Added bank_transaction_id column';
    RAISE NOTICE '- Created indexes for efficient lookups';
END $$;
