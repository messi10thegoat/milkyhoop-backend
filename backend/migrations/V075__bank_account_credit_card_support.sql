-- ============================================================================
-- V075: Bank Account Credit Card Support
-- ============================================================================
-- Purpose: Add credit_card account type and CoA parent for credit cards
-- ============================================================================

-- ============================================================================
-- 1. ADD CREDIT CARD ACCOUNT TYPE TO CONSTRAINT
-- ============================================================================

-- Drop old constraint and recreate with credit_card type
ALTER TABLE bank_accounts DROP CONSTRAINT IF EXISTS chk_bank_account_type;
ALTER TABLE bank_accounts ADD CONSTRAINT chk_bank_account_type
    CHECK (account_type IN ('bank', 'cash', 'petty_cash', 'e_wallet', 'credit_card'));

-- ============================================================================
-- 2. ADD CREDIT CARD PAYABLE PARENT CoA (2-10600)
-- ============================================================================
-- This parent will hold all credit card accounts (2-106XX)
-- Credit card = LIABILITY (increases when you use it to pay)

INSERT INTO chart_of_accounts (
    id, tenant_id, account_code, name, description,
    account_type, normal_balance, parent_code,
    is_header, is_detail, is_bank_account, is_active, is_system
)
SELECT
    gen_random_uuid(),
    t.tenant_id,
    '2-10600',
    'Hutang Kartu Kredit',
    'Credit card payables - auto-created accounts for credit cards',
    'LIABILITY',
    'CREDIT',
    '2-10000',
    true,   -- is_header (parent)
    false,  -- is_detail
    false,  -- is_bank_account (this is parent, not actual account)
    true,
    true    -- is_system (cannot be deleted)
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts WHERE tenant_id IS NOT NULL) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '2-10600' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 3. ADD TRANSACTION TYPE 'charge' FOR CREDIT CARD USAGE
-- ============================================================================
-- Update constraint to allow 'charge' transaction type for credit cards

ALTER TABLE bank_transactions DROP CONSTRAINT IF EXISTS chk_bank_tx_type;
ALTER TABLE bank_transactions ADD CONSTRAINT chk_bank_tx_type CHECK (transaction_type IN (
    'deposit', 'withdrawal', 'transfer_in', 'transfer_out',
    'adjustment', 'opening', 'payment_received', 'payment_made',
    'fee', 'interest', 'charge'
));

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V075: Credit Card Support completed successfully';
    RAISE NOTICE '- Added credit_card account type';
    RAISE NOTICE '- Created CoA parent 2-10600 Hutang Kartu Kredit';
    RAISE NOTICE '- Added charge transaction type';
END $$;
