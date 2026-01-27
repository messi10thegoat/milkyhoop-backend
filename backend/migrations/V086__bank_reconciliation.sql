-- ============================================================================
-- V086: Enhanced Bank Reconciliation Module
-- ============================================================================
-- Purpose: Enhanced bank reconciliation with statement matching and adjustments
-- Creates: reconciliation_sessions, bank_statement_lines, reconciliation_matches,
--          reconciliation_adjustments
-- Extends: bank_transactions with additional reconciliation columns
-- ============================================================================

-- ============================================================================
-- 1. RECONCILIATION SESSIONS TABLE - Main reconciliation sessions
-- ============================================================================

CREATE TABLE IF NOT EXISTS reconciliation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    account_id UUID NOT NULL REFERENCES bank_accounts(id),

    -- Statement period
    statement_date DATE NOT NULL,
    statement_start_date DATE NOT NULL,
    statement_end_date DATE NOT NULL,

    -- Balances (BIGINT for IDR - smallest currency unit)
    statement_beginning_balance BIGINT NOT NULL,
    statement_ending_balance BIGINT NOT NULL,
    cleared_balance BIGINT,
    difference BIGINT,

    -- Status
    status VARCHAR(20) DEFAULT 'not_started',

    -- Audit
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    CONSTRAINT chk_recon_session_status CHECK (status IN ('not_started', 'in_progress', 'completed', 'cancelled'))
);

COMMENT ON TABLE reconciliation_sessions IS 'Bank reconciliation sessions - tracks statement matching progress';
COMMENT ON COLUMN reconciliation_sessions.cleared_balance IS 'Sum of matched/cleared transactions';
COMMENT ON COLUMN reconciliation_sessions.difference IS 'statement_ending_balance - cleared_balance';

-- ============================================================================
-- 2. BANK STATEMENT LINES TABLE - Imported statement data
-- ============================================================================

CREATE TABLE IF NOT EXISTS bank_statement_lines_v2 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES reconciliation_sessions(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,

    -- Statement line data
    date DATE NOT NULL,
    description VARCHAR(500) NOT NULL,
    reference VARCHAR(100),

    -- Amount (BIGINT for IDR)
    amount BIGINT NOT NULL,
    type VARCHAR(10) NOT NULL,
    running_balance BIGINT,

    -- Matching status
    match_status VARCHAR(20) DEFAULT 'unmatched',
    match_confidence VARCHAR(10),
    match_difference BIGINT,

    -- Raw data for debugging/audit
    raw_data JSONB,

    CONSTRAINT chk_stmt_line_type CHECK (type IN ('debit', 'credit')),
    CONSTRAINT chk_stmt_line_match_status CHECK (match_status IN ('matched', 'unmatched', 'partially_matched', 'excluded')),
    CONSTRAINT chk_stmt_line_confidence CHECK (match_confidence IS NULL OR match_confidence IN ('exact', 'high', 'medium', 'low', 'manual'))
);

COMMENT ON TABLE bank_statement_lines_v2 IS 'Imported bank statement lines for reconciliation';
COMMENT ON COLUMN bank_statement_lines_v2.match_status IS 'matched=fully matched, unmatched=no match, partially_matched=partial, excluded=intentionally ignored';
COMMENT ON COLUMN bank_statement_lines_v2.match_confidence IS 'Confidence level of automatic matching';

-- ============================================================================
-- 3. RECONCILIATION MATCHES TABLE - Links between statement lines and transactions
-- ============================================================================

CREATE TABLE IF NOT EXISTS reconciliation_matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES reconciliation_sessions(id) ON DELETE CASCADE,
    statement_line_id UUID NOT NULL REFERENCES bank_statement_lines_v2(id) ON DELETE CASCADE,
    transaction_id UUID NOT NULL REFERENCES bank_transactions(id),
    tenant_id TEXT NOT NULL,

    -- Match type
    match_type VARCHAR(20) NOT NULL,

    -- Confidence level
    confidence VARCHAR(10),

    -- Audit
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Prevent duplicate matches
    CONSTRAINT uq_recon_match_stmt_txn UNIQUE (statement_line_id, transaction_id),
    CONSTRAINT chk_recon_match_type CHECK (match_type IN ('one_to_one', 'one_to_many', 'many_to_one')),
    CONSTRAINT chk_recon_match_confidence CHECK (confidence IS NULL OR confidence IN ('exact', 'high', 'medium', 'low', 'manual'))
);

COMMENT ON TABLE reconciliation_matches IS 'Links statement lines to bank transactions';
COMMENT ON COLUMN reconciliation_matches.match_type IS 'one_to_one=single match, one_to_many=one stmt to many txns, many_to_one=many stmts to one txn';

-- ============================================================================
-- 4. RECONCILIATION ADJUSTMENTS TABLE - Manual adjustments during reconciliation
-- ============================================================================

CREATE TABLE IF NOT EXISTS reconciliation_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES reconciliation_sessions(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,

    -- Adjustment details
    type VARCHAR(20) NOT NULL,
    amount BIGINT NOT NULL,
    description VARCHAR(500) NOT NULL,

    -- Account for the adjustment
    account_id UUID NOT NULL,

    -- Journal entry created for this adjustment (after posting)
    journal_entry_id UUID,

    -- Audit
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_recon_adj_type CHECK (type IN ('bank_fee', 'interest', 'correction', 'other'))
);

COMMENT ON TABLE reconciliation_adjustments IS 'Manual adjustments made during reconciliation (fees, interest, corrections)';

-- ============================================================================
-- 5. EXTEND BANK_TRANSACTIONS TABLE
-- ============================================================================

DO $$
BEGIN
    -- Add is_cleared column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'is_cleared'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN is_cleared BOOLEAN DEFAULT FALSE;
    END IF;

    -- Add reconciled_session_id column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'reconciled_session_id'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN reconciled_session_id UUID;
    END IF;

    -- Add matched_statement_line_id column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'matched_statement_line_id'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN matched_statement_line_id UUID;
    END IF;

    -- Add cleared_at column if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'cleared_at'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN cleared_at TIMESTAMPTZ;
    END IF;

    -- Note: is_reconciled and reconciled_at already exist from V034
END $$;

COMMENT ON COLUMN bank_transactions.is_cleared IS 'Whether transaction is cleared (matched to statement line)';
COMMENT ON COLUMN bank_transactions.reconciled_session_id IS 'Link to the reconciliation session that finalized this transaction';
COMMENT ON COLUMN bank_transactions.matched_statement_line_id IS 'Link to the matched bank statement line';
COMMENT ON COLUMN bank_transactions.cleared_at IS 'Timestamp when transaction was cleared';

-- ============================================================================
-- 6. INDEXES
-- ============================================================================

-- Reconciliation sessions indexes
CREATE INDEX IF NOT EXISTS idx_recon_sessions_tenant_account ON reconciliation_sessions(tenant_id, account_id);
CREATE INDEX IF NOT EXISTS idx_recon_sessions_tenant_status ON reconciliation_sessions(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_recon_sessions_tenant_date ON reconciliation_sessions(tenant_id, statement_date DESC);

-- Bank statement lines indexes
CREATE INDEX IF NOT EXISTS idx_stmt_lines_v2_session ON bank_statement_lines_v2(session_id);
CREATE INDEX IF NOT EXISTS idx_stmt_lines_v2_tenant ON bank_statement_lines_v2(tenant_id);
CREATE INDEX IF NOT EXISTS idx_stmt_lines_v2_date ON bank_statement_lines_v2(tenant_id, date);
CREATE INDEX IF NOT EXISTS idx_stmt_lines_v2_status ON bank_statement_lines_v2(tenant_id, match_status);

-- Reconciliation matches indexes
CREATE INDEX IF NOT EXISTS idx_recon_matches_session ON reconciliation_matches(session_id);
CREATE INDEX IF NOT EXISTS idx_recon_matches_stmt_line ON reconciliation_matches(statement_line_id);
CREATE INDEX IF NOT EXISTS idx_recon_matches_transaction ON reconciliation_matches(transaction_id);
CREATE INDEX IF NOT EXISTS idx_recon_matches_tenant ON reconciliation_matches(tenant_id);

-- Reconciliation adjustments index
CREATE INDEX IF NOT EXISTS idx_recon_adjustments_session ON reconciliation_adjustments(session_id);
CREATE INDEX IF NOT EXISTS idx_recon_adjustments_tenant ON reconciliation_adjustments(tenant_id);

-- Bank transactions - additional index for reconciliation queries
CREATE INDEX IF NOT EXISTS idx_bank_tx_cleared_reconciled ON bank_transactions(tenant_id, is_cleared, is_reconciled);

-- ============================================================================
-- 7. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE reconciliation_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_statement_lines_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconciliation_matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconciliation_adjustments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_reconciliation_sessions ON reconciliation_sessions;
DROP POLICY IF EXISTS rls_bank_statement_lines_v2 ON bank_statement_lines_v2;
DROP POLICY IF EXISTS rls_reconciliation_matches ON reconciliation_matches;
DROP POLICY IF EXISTS rls_reconciliation_adjustments ON reconciliation_adjustments;

CREATE POLICY rls_reconciliation_sessions ON reconciliation_sessions
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bank_statement_lines_v2 ON bank_statement_lines_v2
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_reconciliation_matches ON reconciliation_matches
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_reconciliation_adjustments ON reconciliation_adjustments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 8. TRIGGERS - updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION update_reconciliation_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reconciliation_sessions_updated_at ON reconciliation_sessions;
CREATE TRIGGER trg_reconciliation_sessions_updated_at
    BEFORE UPDATE ON reconciliation_sessions
    FOR EACH ROW EXECUTE FUNCTION update_reconciliation_sessions_updated_at();

-- Note: bank_statement_lines_v2 does not have updated_at column per spec,
-- but adding trigger function for future use if needed

-- ============================================================================
-- 9. HELPER FUNCTION - Update reconciliation session stats
-- ============================================================================

CREATE OR REPLACE FUNCTION update_reconciliation_session_stats(p_session_id UUID)
RETURNS VOID AS $$
DECLARE
    v_cleared_balance BIGINT;
    v_statement_ending_balance BIGINT;
BEGIN
    -- Get the statement ending balance
    SELECT statement_ending_balance INTO v_statement_ending_balance
    FROM reconciliation_sessions
    WHERE id = p_session_id;

    -- Calculate cleared balance from matched statement lines
    -- Sum amounts: credit adds to balance, debit subtracts
    SELECT COALESCE(SUM(
        CASE WHEN bsl.type = 'credit' THEN bsl.amount
             WHEN bsl.type = 'debit' THEN -bsl.amount
             ELSE 0
        END
    ), 0) INTO v_cleared_balance
    FROM bank_statement_lines_v2 bsl
    WHERE bsl.session_id = p_session_id
      AND bsl.match_status = 'matched';

    -- Update the session with calculated values
    UPDATE reconciliation_sessions
    SET cleared_balance = v_cleared_balance,
        difference = v_statement_ending_balance - v_cleared_balance,
        updated_at = NOW()
    WHERE id = p_session_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_reconciliation_session_stats IS 'Recalculates cleared_balance and difference for a reconciliation session';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V086: Enhanced Bank Reconciliation Module created successfully';
    RAISE NOTICE 'Tables: reconciliation_sessions, bank_statement_lines_v2, reconciliation_matches, reconciliation_adjustments';
    RAISE NOTICE 'Extended: bank_transactions with is_cleared, reconciled_session_id, matched_statement_line_id, cleared_at';
    RAISE NOTICE 'RLS enabled on all new tables';
    RAISE NOTICE 'Helper function: update_reconciliation_session_stats(session_id)';
END $$;
