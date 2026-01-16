-- ============================================================================
-- V034: Bank Accounts and Bank Transfers Module
-- ============================================================================
-- Purpose: Bank account master data and inter-bank transfer management
-- Integrates with CoA for cash/bank accounts
-- ============================================================================

-- ============================================================================
-- 1. ADD BANK TRANSFER FEE ACCOUNT IF NOT EXISTS
-- ============================================================================

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '5-20950',
    'Biaya Transfer Bank',
    'EXPENSE',
    'DEBIT',
    '5-20000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '5-20950' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 2. BANK ACCOUNTS TABLE - Master bank account data
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Bank account identification
    account_name VARCHAR(100) NOT NULL,
    account_number VARCHAR(50),
    bank_name VARCHAR(100),
    bank_branch VARCHAR(100),
    swift_code VARCHAR(20),

    -- Link to Chart of Accounts (REQUIRED - must be ASSET type cash/bank)
    coa_id UUID NOT NULL,

    -- Balance tracking (denormalized for performance)
    opening_balance BIGINT DEFAULT 0,
    current_balance BIGINT DEFAULT 0,
    last_reconciled_balance BIGINT DEFAULT 0,
    last_reconciled_date DATE,

    -- Account details
    account_type VARCHAR(20) DEFAULT 'bank',
    currency CHAR(3) DEFAULT 'IDR',

    -- Status
    is_active BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,

    -- Metadata
    notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_bank_accounts_tenant_name UNIQUE(tenant_id, account_name),
    CONSTRAINT uq_bank_accounts_tenant_coa UNIQUE(tenant_id, coa_id),
    CONSTRAINT chk_bank_account_type CHECK (account_type IN ('bank', 'cash', 'petty_cash', 'e_wallet'))
);

COMMENT ON TABLE bank_accounts IS 'Rekening Bank - Master data for bank/cash accounts';
COMMENT ON COLUMN bank_accounts.coa_id IS 'Link to chart_of_accounts - must be ASSET type (1-10100 Kas or 1-10200 Bank)';
COMMENT ON COLUMN bank_accounts.current_balance IS 'Denormalized balance - updated via triggers on bank_transactions';

-- ============================================================================
-- 3. BANK TRANSACTIONS TABLE - Transaction log for all bank movements
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    bank_account_id UUID NOT NULL REFERENCES bank_accounts(id),

    -- Transaction details
    transaction_date DATE NOT NULL,
    transaction_type VARCHAR(30) NOT NULL,

    -- Amount (positive = in, negative = out)
    amount BIGINT NOT NULL,
    running_balance BIGINT NOT NULL,

    -- Reference
    reference_type VARCHAR(30),
    reference_id UUID,
    reference_number VARCHAR(100),

    -- Description
    description TEXT,
    payee_payer VARCHAR(255),

    -- Reconciliation
    is_reconciled BOOLEAN DEFAULT false,
    reconciled_at TIMESTAMPTZ,
    reconciled_by UUID,

    -- Accounting link
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT chk_bank_tx_type CHECK (transaction_type IN (
        'deposit', 'withdrawal', 'transfer_in', 'transfer_out',
        'adjustment', 'opening', 'payment_received', 'payment_made',
        'fee', 'interest'
    ))
);

COMMENT ON TABLE bank_transactions IS 'Log semua transaksi bank/kas';
COMMENT ON COLUMN bank_transactions.running_balance IS 'Saldo setelah transaksi ini';

-- ============================================================================
-- 4. BANK TRANSFERS TABLE - Inter-bank transfers
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Transfer identification
    transfer_number VARCHAR(50) NOT NULL,

    -- Source and destination
    from_bank_id UUID NOT NULL REFERENCES bank_accounts(id),
    to_bank_id UUID NOT NULL REFERENCES bank_accounts(id),

    -- Amounts
    amount BIGINT NOT NULL,
    fee_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,

    -- Fee account (defaults to 5-20950)
    fee_account_id UUID,

    -- Status: draft -> posted -> void
    status VARCHAR(20) DEFAULT 'draft',

    -- Dates
    transfer_date DATE NOT NULL,

    -- Reference
    ref_no VARCHAR(100),
    notes TEXT,

    -- Accounting links
    journal_id UUID,
    from_transaction_id UUID,
    to_transaction_id UUID,

    -- Status tracking
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    voided_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_bank_transfers_tenant_number UNIQUE(tenant_id, transfer_number),
    CONSTRAINT chk_bank_transfer_status CHECK (status IN ('draft', 'posted', 'void')),
    CONSTRAINT chk_different_banks CHECK (from_bank_id != to_bank_id)
);

COMMENT ON TABLE bank_transfers IS 'Transfer antar rekening bank';
COMMENT ON COLUMN bank_transfers.total_amount IS 'Total yang didebit dari rekening asal = amount + fee_amount';

-- ============================================================================
-- 5. SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_transfer_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'TRF',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_bt_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 6. INDEXES
-- ============================================================================

-- Bank accounts
CREATE INDEX IF NOT EXISTS idx_bank_acc_tenant ON bank_accounts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_bank_acc_tenant_active ON bank_accounts(tenant_id, is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_bank_acc_coa ON bank_accounts(coa_id);
CREATE INDEX IF NOT EXISTS idx_bank_acc_name ON bank_accounts(tenant_id, account_name);

-- Bank transactions
CREATE INDEX IF NOT EXISTS idx_bank_tx_account ON bank_transactions(bank_account_id);
CREATE INDEX IF NOT EXISTS idx_bank_tx_tenant_date ON bank_transactions(tenant_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_bank_tx_type ON bank_transactions(tenant_id, transaction_type);
CREATE INDEX IF NOT EXISTS idx_bank_tx_reference ON bank_transactions(reference_type, reference_id);
CREATE INDEX IF NOT EXISTS idx_bank_tx_reconciled ON bank_transactions(tenant_id, is_reconciled) WHERE is_reconciled = false;

-- Bank transfers
CREATE INDEX IF NOT EXISTS idx_bank_trf_tenant_status ON bank_transfers(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_bank_trf_tenant_date ON bank_transfers(tenant_id, transfer_date DESC);
CREATE INDEX IF NOT EXISTS idx_bank_trf_from ON bank_transfers(from_bank_id);
CREATE INDEX IF NOT EXISTS idx_bank_trf_to ON bank_transfers(to_bank_id);
CREATE INDEX IF NOT EXISTS idx_bank_trf_number ON bank_transfers(tenant_id, transfer_number);

-- ============================================================================
-- 7. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE bank_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_transfers ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_transfer_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_bank_accounts ON bank_accounts;
DROP POLICY IF EXISTS rls_bank_transactions ON bank_transactions;
DROP POLICY IF EXISTS rls_bank_transfers ON bank_transfers;
DROP POLICY IF EXISTS rls_bank_transfer_sequences ON bank_transfer_sequences;

CREATE POLICY rls_bank_accounts ON bank_accounts
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bank_transactions ON bank_transactions
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bank_transfers ON bank_transfers
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bank_transfer_sequences ON bank_transfer_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 8. FUNCTIONS
-- ============================================================================

-- Generate bank transfer number
CREATE OR REPLACE FUNCTION generate_bank_transfer_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'TRF'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_transfer_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO bank_transfer_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = bank_transfer_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: TRF-YYMM-0001
    v_transfer_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_transfer_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_bank_transfer_number IS 'Generates sequential bank transfer number per tenant per month';

-- Update bank account balance (called after insert on bank_transactions)
CREATE OR REPLACE FUNCTION update_bank_account_balance()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the current_balance on bank_accounts
    UPDATE bank_accounts
    SET current_balance = NEW.running_balance,
        updated_at = NOW()
    WHERE id = NEW.bank_account_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Calculate running balance for new transaction
CREATE OR REPLACE FUNCTION calculate_bank_running_balance(
    p_bank_account_id UUID,
    p_amount BIGINT
) RETURNS BIGINT AS $$
DECLARE
    v_current_balance BIGINT;
BEGIN
    SELECT current_balance INTO v_current_balance
    FROM bank_accounts
    WHERE id = p_bank_account_id;

    RETURN COALESCE(v_current_balance, 0) + p_amount;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION calculate_bank_running_balance IS 'Calculate running balance for a new bank transaction';

-- ============================================================================
-- 9. TRIGGERS
-- ============================================================================

-- Trigger to update bank account balance after transaction
DROP TRIGGER IF EXISTS trg_update_bank_balance ON bank_transactions;
CREATE TRIGGER trg_update_bank_balance
    AFTER INSERT ON bank_transactions
    FOR EACH ROW EXECUTE FUNCTION update_bank_account_balance();

-- updated_at triggers
CREATE OR REPLACE FUNCTION update_bank_accounts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bank_accounts_updated_at ON bank_accounts;
CREATE TRIGGER trg_bank_accounts_updated_at
    BEFORE UPDATE ON bank_accounts
    FOR EACH ROW EXECUTE FUNCTION update_bank_accounts_updated_at();

DROP TRIGGER IF EXISTS trg_bank_transfers_updated_at ON bank_transfers;
CREATE TRIGGER trg_bank_transfers_updated_at
    BEFORE UPDATE ON bank_transfers
    FOR EACH ROW EXECUTE FUNCTION update_bank_accounts_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V034: Bank Accounts and Bank Transfers created successfully';
    RAISE NOTICE 'Tables: bank_accounts, bank_transactions, bank_transfers, bank_transfer_sequences';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
