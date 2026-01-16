-- ============================================================================
-- V032: Opening Balance Mechanism - FIXED
-- ============================================================================
-- Purpose: Create infrastructure for recording opening balances during
--          tenant onboarding or fiscal year transitions
-- Adapted to actual database schema
-- ============================================================================

-- ============================================================================
-- 1. ADD OPENING BALANCE EQUITY ACCOUNT
-- ============================================================================

-- Insert Opening Balance Equity account (3-50000) for all tenants that have CoA
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '3-50000',
    'Modal Saldo Awal',
    'EQUITY',
    'CREDIT',
    '3-00000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '3-50000' AND tenant_id = t.tenant_id
);

COMMENT ON TABLE chart_of_accounts IS 'Chart of Accounts - includes Opening Balance Equity (3-50000) for opening balance entries';

-- ============================================================================
-- 2. ADD IS_OPENING_BALANCE FLAG TO JOURNAL_ENTRIES
-- ============================================================================

ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS is_opening_balance BOOLEAN DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_journal_opening ON journal_entries(tenant_id, is_opening_balance)
    WHERE is_opening_balance = true;

COMMENT ON COLUMN journal_entries.is_opening_balance IS 'True if this journal entry is an opening balance entry';

-- ============================================================================
-- 3. OPENING BALANCE RECORDS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS opening_balance_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Opening balance setup info
    opening_date DATE NOT NULL,
    description TEXT,

    -- Linked journals
    gl_journal_id UUID REFERENCES journal_entries(id),
    ar_journal_id UUID,
    ap_journal_id UUID,
    inventory_journal_id UUID,

    -- Snapshot of all balances for audit
    balance_snapshot JSONB NOT NULL,

    -- Status: ACTIVE = current, SUPERSEDED = replaced by new opening balance
    status VARCHAR(20) DEFAULT 'ACTIVE',
    superseded_by UUID REFERENCES opening_balance_records(id),
    superseded_at TIMESTAMPTZ,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_ob_status CHECK (status IN ('ACTIVE', 'SUPERSEDED'))
);

COMMENT ON TABLE opening_balance_records IS 'Tracks opening balance entries for each tenant - one ACTIVE record per tenant';
COMMENT ON COLUMN opening_balance_records.balance_snapshot IS 'JSON snapshot of all account balances at opening. Format: {accounts: [{code, name, debit, credit}], totals: {debit, credit}}';

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_ob_tenant_status ON opening_balance_records(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ob_tenant_date ON opening_balance_records(tenant_id, opening_date);

-- ============================================================================
-- 5. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE opening_balance_records ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_opening_balance_records ON opening_balance_records;
CREATE POLICY rls_opening_balance_records ON opening_balance_records
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. UPDATED_AT TRIGGER
-- ============================================================================

CREATE OR REPLACE FUNCTION update_opening_balance_records_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ob_updated_at ON opening_balance_records;
CREATE TRIGGER trg_ob_updated_at
    BEFORE UPDATE ON opening_balance_records
    FOR EACH ROW EXECUTE FUNCTION update_opening_balance_records_updated_at();

-- ============================================================================
-- 7. HELPER FUNCTION: Get opening balance equity account ID
-- ============================================================================

CREATE OR REPLACE FUNCTION get_opening_balance_equity_account(p_tenant_id TEXT)
RETURNS UUID AS $$
DECLARE
    v_account_id UUID;
BEGIN
    SELECT id INTO v_account_id
    FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND account_code = '3-50000';

    RETURN v_account_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_opening_balance_equity_account IS 'Returns the Opening Balance Equity account ID for a tenant';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
