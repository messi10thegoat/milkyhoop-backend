-- ============================================
-- V016: Safety Constraints & Trial Balance View
-- ============================================
-- Extra DB-level invariants for audit-grade safety

-- ============================================
-- 1. Reversal cannot reference itself
-- ============================================
ALTER TABLE journal_entries
    ADD CONSTRAINT chk_reversal_not_self
    CHECK (reversal_of_id IS NULL OR reversal_of_id != id);

-- ============================================
-- 2. FK with ON DELETE RESTRICT for reversal chain
--    (prevent accidental deletion of original when reversal exists)
-- ============================================
-- First drop existing FK if exists (from V015)
ALTER TABLE journal_entries
    DROP CONSTRAINT IF EXISTS fk_je_reversal_of;

ALTER TABLE journal_entries
    DROP CONSTRAINT IF EXISTS fk_je_reversed_by;

-- Add strict FK for reversal_of_id
ALTER TABLE journal_entries
    ADD CONSTRAINT fk_je_reversal_of
    FOREIGN KEY (reversal_of_id) REFERENCES journal_entries(id)
    ON DELETE RESTRICT;

-- Add strict FK for reversed_by_id
ALTER TABLE journal_entries
    ADD CONSTRAINT fk_je_reversed_by
    FOREIGN KEY (reversed_by_id) REFERENCES journal_entries(id)
    ON DELETE RESTRICT;

-- ============================================
-- 3. Trial Balance View (materialized for performance)
-- ============================================
-- View calculates account balances from posted journals
CREATE OR REPLACE VIEW v_trial_balance AS
SELECT
    coa.tenant_id,
    coa.id as account_id,
    coa.account_code,
    coa.name as account_name,
    coa.account_type,
    coa.normal_balance,
    COALESCE(SUM(jl.debit), 0) as total_debit,
    COALESCE(SUM(jl.credit), 0) as total_credit,
    CASE
        WHEN coa.normal_balance = 'DEBIT' THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
        ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
    END as balance
FROM chart_of_accounts coa
LEFT JOIN journal_lines jl ON jl.account_id = coa.id
LEFT JOIN journal_entries je ON je.id = jl.journal_id
    AND je.tenant_id = coa.tenant_id
    AND je.status = 'POSTED'
WHERE coa.is_active = true
GROUP BY coa.tenant_id, coa.id, coa.account_code, coa.name, coa.account_type, coa.normal_balance;

-- ============================================
-- 4. Trial Balance by Period (function)
-- ============================================
CREATE OR REPLACE FUNCTION get_trial_balance(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE,
    p_period_id UUID DEFAULT NULL
)
RETURNS TABLE (
    account_id UUID,
    account_code TEXT,
    account_name TEXT,
    account_type TEXT,
    normal_balance TEXT,
    total_debit NUMERIC(18,2),
    total_credit NUMERIC(18,2),
    balance NUMERIC(18,2)
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT
        coa.id as account_id,
        coa.account_code,
        coa.name as account_name,
        coa.account_type,
        coa.normal_balance,
        COALESCE(SUM(jl.debit), 0)::NUMERIC(18,2) as total_debit,
        COALESCE(SUM(jl.credit), 0)::NUMERIC(18,2) as total_credit,
        (CASE
            WHEN coa.normal_balance = 'DEBIT' THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
            ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
        END)::NUMERIC(18,2) as balance
    FROM chart_of_accounts coa
    LEFT JOIN journal_lines jl ON jl.account_id = coa.id
    LEFT JOIN journal_entries je ON je.id = jl.journal_id
        AND je.tenant_id = p_tenant_id
        AND je.status = 'POSTED'
        AND je.journal_date <= p_as_of_date
        AND (p_period_id IS NULL OR je.period_id = p_period_id)
    WHERE coa.tenant_id = p_tenant_id
      AND coa.is_active = true
    GROUP BY coa.id, coa.account_code, coa.name, coa.account_type, coa.normal_balance
    HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0
    ORDER BY coa.account_code;
END;
$$;

-- ============================================
-- 5. Report Balances Cache Table (for future scaling)
-- ============================================
CREATE TABLE IF NOT EXISTS report_balance_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    period_id UUID REFERENCES fiscal_periods(id),
    account_id UUID NOT NULL REFERENCES chart_of_accounts(id),
    balance_date DATE NOT NULL,

    -- Cached values
    total_debit NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_credit NUMERIC(18,2) NOT NULL DEFAULT 0,
    balance NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Cache metadata
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    invalidated_at TIMESTAMPTZ,
    is_valid BOOLEAN NOT NULL DEFAULT true,

    -- Unique per tenant+period+account+date
    CONSTRAINT uq_rbc_tenant_period_account_date
        UNIQUE (tenant_id, period_id, account_id, balance_date)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_rbc_tenant_valid
    ON report_balance_cache(tenant_id, is_valid)
    WHERE is_valid = true;

CREATE INDEX IF NOT EXISTS idx_rbc_period
    ON report_balance_cache(period_id);

-- RLS for tenant isolation
ALTER TABLE report_balance_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_report_balance_cache ON report_balance_cache
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================
-- 6. Function to invalidate cache on journal change
-- ============================================
CREATE OR REPLACE FUNCTION invalidate_balance_cache()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Invalidate cache for the affected tenant and accounts
    UPDATE report_balance_cache
    SET is_valid = false, invalidated_at = NOW()
    WHERE tenant_id = NEW.tenant_id
      AND is_valid = true
      AND (period_id IS NULL OR period_id IN (
          SELECT id FROM fiscal_periods
          WHERE tenant_id = NEW.tenant_id
            AND NEW.journal_date BETWEEN start_date AND end_date
      ));

    RETURN NEW;
END;
$$;

-- Trigger on journal_entries (optional - enable when needed)
-- CREATE TRIGGER trg_invalidate_cache_on_journal
--     AFTER INSERT OR UPDATE ON journal_entries
--     FOR EACH ROW
--     EXECUTE FUNCTION invalidate_balance_cache();

-- ============================================
-- Done
-- ============================================
COMMENT ON VIEW v_trial_balance IS
    'Real-time trial balance view. Use get_trial_balance() for filtered queries.';

COMMENT ON FUNCTION get_trial_balance IS
    'Get trial balance as of a specific date, optionally filtered by period.';

COMMENT ON TABLE report_balance_cache IS
    'Read-model cache for report balances. Enable trigger when >100k journal lines.';
