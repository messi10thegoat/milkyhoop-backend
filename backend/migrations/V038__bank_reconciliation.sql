-- ============================================================================
-- V038: Bank Reconciliation Module
-- ============================================================================
-- Purpose: Reconcile bank account transactions with bank statements
-- Uses existing bank_transactions.is_reconciled from V034
-- ============================================================================

-- ============================================================================
-- 1. BANK RECONCILIATIONS TABLE - Reconciliation sessions
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_reconciliations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Reference
    bank_account_id UUID NOT NULL REFERENCES bank_accounts(id),
    reconciliation_number VARCHAR(50) NOT NULL,

    -- Period
    statement_date DATE NOT NULL,
    statement_start_date DATE NOT NULL,
    statement_end_date DATE NOT NULL,

    -- Balances (BIGINT - smallest currency unit)
    statement_opening_balance BIGINT NOT NULL,
    statement_closing_balance BIGINT NOT NULL,
    system_opening_balance BIGINT NOT NULL,
    system_closing_balance BIGINT NOT NULL,

    -- Reconciled amounts
    reconciled_deposits BIGINT DEFAULT 0,
    reconciled_withdrawals BIGINT DEFAULT 0,
    unreconciled_deposits BIGINT DEFAULT 0,
    unreconciled_withdrawals BIGINT DEFAULT 0,

    -- Difference
    difference BIGINT DEFAULT 0,

    -- Status
    status VARCHAR(20) DEFAULT 'in_progress', -- in_progress, completed, void

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    completed_at TIMESTAMPTZ,
    completed_by UUID,

    CONSTRAINT uq_bank_reconciliations_number UNIQUE(tenant_id, reconciliation_number),
    CONSTRAINT chk_recon_status CHECK (status IN ('in_progress', 'completed', 'void'))
);

COMMENT ON TABLE bank_reconciliations IS 'Bank reconciliation sessions';

-- ============================================================================
-- 2. BANK RECONCILIATION ITEMS TABLE - Individual transaction matching
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_reconciliation_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reconciliation_id UUID NOT NULL REFERENCES bank_reconciliations(id) ON DELETE CASCADE,

    -- Link to bank transaction
    bank_transaction_id UUID NOT NULL REFERENCES bank_transactions(id),

    -- Reconciliation status
    is_matched BOOLEAN DEFAULT false,
    matched_at TIMESTAMPTZ,
    matched_by UUID,

    -- Statement match info (for imported statements)
    statement_reference VARCHAR(100),
    statement_amount BIGINT,
    statement_date DATE,

    -- Adjustment (if amounts differ)
    adjustment_amount BIGINT DEFAULT 0,
    adjustment_journal_id UUID,
    adjustment_reason TEXT
);

COMMENT ON TABLE bank_reconciliation_items IS 'Individual transactions in reconciliation';

-- ============================================================================
-- 3. BANK STATEMENT IMPORTS TABLE - Optional file imports
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_statement_imports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    bank_account_id UUID NOT NULL REFERENCES bank_accounts(id),

    -- Import info
    import_date TIMESTAMPTZ DEFAULT NOW(),
    file_name VARCHAR(255),
    file_type VARCHAR(20), -- csv, ofx, qif, mt940

    -- Period
    statement_start_date DATE,
    statement_end_date DATE,

    -- Stats
    total_transactions INTEGER DEFAULT 0,
    matched_transactions INTEGER DEFAULT 0,
    unmatched_transactions INTEGER DEFAULT 0,

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, processing, completed, failed
    error_message TEXT,

    created_by UUID,

    CONSTRAINT chk_import_status CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
);

-- ============================================================================
-- 4. BANK STATEMENT LINES TABLE - Imported statement data
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_statement_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id UUID NOT NULL REFERENCES bank_statement_imports(id) ON DELETE CASCADE,

    -- Statement data
    transaction_date DATE NOT NULL,
    value_date DATE,
    reference VARCHAR(100),
    description TEXT,
    debit_amount BIGINT DEFAULT 0,
    credit_amount BIGINT DEFAULT 0,
    balance BIGINT,

    -- Matching
    is_matched BOOLEAN DEFAULT false,
    matched_transaction_id UUID REFERENCES bank_transactions(id),
    match_confidence DECIMAL(5,2), -- 0-100 percentage

    -- Row info
    row_number INTEGER,
    raw_data JSONB
);

-- ============================================================================
-- 5. SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_reconciliation_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'REC',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_recon_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 6. SEED BANK ADJUSTMENT ACCOUNT (Optional)
-- ============================================================================

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '6-90100',
    'Koreksi Bank',
    'OTHER_EXPENSE',
    'DEBIT',
    '6-90000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '6-90100' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 7. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_bank_recon_tenant ON bank_reconciliations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_bank_recon_account ON bank_reconciliations(bank_account_id, statement_date);
CREATE INDEX IF NOT EXISTS idx_bank_recon_status ON bank_reconciliations(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_bank_recon_items_txn ON bank_reconciliation_items(bank_transaction_id);
CREATE INDEX IF NOT EXISTS idx_bank_recon_items_recon ON bank_reconciliation_items(reconciliation_id);
CREATE INDEX IF NOT EXISTS idx_bank_stmt_imports_account ON bank_statement_imports(bank_account_id);
CREATE INDEX IF NOT EXISTS idx_bank_stmt_lines_date ON bank_statement_lines(transaction_date);
CREATE INDEX IF NOT EXISTS idx_bank_stmt_lines_import ON bank_statement_lines(import_id);

-- ============================================================================
-- 8. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE bank_reconciliations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_reconciliation_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_statement_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_statement_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_reconciliation_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_bank_reconciliations ON bank_reconciliations;
DROP POLICY IF EXISTS rls_bank_reconciliation_items ON bank_reconciliation_items;
DROP POLICY IF EXISTS rls_bank_statement_imports ON bank_statement_imports;
DROP POLICY IF EXISTS rls_bank_statement_lines ON bank_statement_lines;
DROP POLICY IF EXISTS rls_bank_reconciliation_sequences ON bank_reconciliation_sequences;

CREATE POLICY rls_bank_reconciliations ON bank_reconciliations
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bank_reconciliation_items ON bank_reconciliation_items
    FOR ALL USING (reconciliation_id IN (
        SELECT id FROM bank_reconciliations WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_bank_statement_imports ON bank_statement_imports
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bank_statement_lines ON bank_statement_lines
    FOR ALL USING (import_id IN (
        SELECT id FROM bank_statement_imports WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_bank_reconciliation_sequences ON bank_reconciliation_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 9. FUNCTIONS
-- ============================================================================

-- Generate reconciliation number
CREATE OR REPLACE FUNCTION generate_reconciliation_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'REC'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_recon_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO bank_reconciliation_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = bank_reconciliation_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_recon_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_recon_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 10. TRIGGERS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_bank_reconciliations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bank_reconciliations_updated_at ON bank_reconciliations;
CREATE TRIGGER trg_bank_reconciliations_updated_at
BEFORE UPDATE ON bank_reconciliations
FOR EACH ROW EXECUTE FUNCTION update_bank_reconciliations_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V038: Bank Reconciliation Module created successfully';
    RAISE NOTICE 'Tables: bank_reconciliations, bank_reconciliation_items, bank_statement_imports, bank_statement_lines';
    RAISE NOTICE 'Seeded account: 6-90100 Koreksi Bank';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
