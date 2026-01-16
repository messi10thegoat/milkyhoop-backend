-- =============================================
-- V051: Budgets (Anggaran)
-- Purpose: Budget planning and variance analysis
-- IMPORTANT: NO journal entries - planning data only
-- =============================================

-- Budget master
CREATE TABLE budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Budget info
    name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Period
    fiscal_year INTEGER NOT NULL,
    budget_type VARCHAR(20) DEFAULT 'annual', -- annual, quarterly, monthly

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, approved, active, closed

    -- Approval
    approved_at TIMESTAMPTZ,
    approved_by UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_budgets_year UNIQUE(tenant_id, fiscal_year, name)
);

-- Budget line items per account
CREATE TABLE budget_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id UUID NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,

    -- Account
    account_id UUID NOT NULL REFERENCES chart_of_accounts(id),

    -- Optional cost center
    cost_center_id UUID REFERENCES cost_centers(id),

    -- Monthly amounts (BIGINT - smallest currency unit)
    jan_amount BIGINT DEFAULT 0,
    feb_amount BIGINT DEFAULT 0,
    mar_amount BIGINT DEFAULT 0,
    apr_amount BIGINT DEFAULT 0,
    may_amount BIGINT DEFAULT 0,
    jun_amount BIGINT DEFAULT 0,
    jul_amount BIGINT DEFAULT 0,
    aug_amount BIGINT DEFAULT 0,
    sep_amount BIGINT DEFAULT 0,
    oct_amount BIGINT DEFAULT 0,
    nov_amount BIGINT DEFAULT 0,
    dec_amount BIGINT DEFAULT 0,

    -- Totals (computed)
    annual_amount BIGINT GENERATED ALWAYS AS (
        jan_amount + feb_amount + mar_amount + apr_amount +
        may_amount + jun_amount + jul_amount + aug_amount +
        sep_amount + oct_amount + nov_amount + dec_amount
    ) STORED,

    notes TEXT,

    CONSTRAINT uq_budget_items UNIQUE(budget_id, account_id, cost_center_id)
);

-- Budget revisions (track changes)
CREATE TABLE budget_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id UUID NOT NULL REFERENCES budgets(id),

    revision_number INTEGER NOT NULL,
    revision_date DATE NOT NULL,
    reason TEXT,

    -- Snapshot of previous values
    previous_data JSONB,

    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_revisions ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_budgets ON budgets
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_budget_items ON budget_items
    USING (budget_id IN (SELECT id FROM budgets WHERE tenant_id = current_setting('app.tenant_id', true)));
CREATE POLICY rls_budget_revisions ON budget_revisions
    USING (budget_id IN (SELECT id FROM budgets WHERE tenant_id = current_setting('app.tenant_id', true)));

-- Indexes
CREATE INDEX idx_budgets_tenant ON budgets(tenant_id);
CREATE INDEX idx_budgets_year ON budgets(tenant_id, fiscal_year);
CREATE INDEX idx_budgets_status ON budgets(tenant_id, status);
CREATE INDEX idx_budget_items_account ON budget_items(account_id);
CREATE INDEX idx_budget_items_cost_center ON budget_items(cost_center_id) WHERE cost_center_id IS NOT NULL;

-- =============================================
-- Helper Functions
-- =============================================

-- Get budget amount for specific month
CREATE OR REPLACE FUNCTION get_budget_month_amount(
    p_budget_item budget_items,
    p_month INTEGER
)
RETURNS BIGINT AS $$
BEGIN
    RETURN CASE p_month
        WHEN 1 THEN p_budget_item.jan_amount
        WHEN 2 THEN p_budget_item.feb_amount
        WHEN 3 THEN p_budget_item.mar_amount
        WHEN 4 THEN p_budget_item.apr_amount
        WHEN 5 THEN p_budget_item.may_amount
        WHEN 6 THEN p_budget_item.jun_amount
        WHEN 7 THEN p_budget_item.jul_amount
        WHEN 8 THEN p_budget_item.aug_amount
        WHEN 9 THEN p_budget_item.sep_amount
        WHEN 10 THEN p_budget_item.oct_amount
        WHEN 11 THEN p_budget_item.nov_amount
        WHEN 12 THEN p_budget_item.dec_amount
        ELSE 0
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Get YTD budget amount
CREATE OR REPLACE FUNCTION get_budget_ytd_amount(
    p_budget_item budget_items,
    p_month INTEGER
)
RETURNS BIGINT AS $$
BEGIN
    RETURN
        CASE WHEN p_month >= 1 THEN p_budget_item.jan_amount ELSE 0 END +
        CASE WHEN p_month >= 2 THEN p_budget_item.feb_amount ELSE 0 END +
        CASE WHEN p_month >= 3 THEN p_budget_item.mar_amount ELSE 0 END +
        CASE WHEN p_month >= 4 THEN p_budget_item.apr_amount ELSE 0 END +
        CASE WHEN p_month >= 5 THEN p_budget_item.may_amount ELSE 0 END +
        CASE WHEN p_month >= 6 THEN p_budget_item.jun_amount ELSE 0 END +
        CASE WHEN p_month >= 7 THEN p_budget_item.jul_amount ELSE 0 END +
        CASE WHEN p_month >= 8 THEN p_budget_item.aug_amount ELSE 0 END +
        CASE WHEN p_month >= 9 THEN p_budget_item.sep_amount ELSE 0 END +
        CASE WHEN p_month >= 10 THEN p_budget_item.oct_amount ELSE 0 END +
        CASE WHEN p_month >= 11 THEN p_budget_item.nov_amount ELSE 0 END +
        CASE WHEN p_month >= 12 THEN p_budget_item.dec_amount ELSE 0 END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Budget vs Actual report
CREATE OR REPLACE FUNCTION get_budget_vs_actual(
    p_budget_id UUID,
    p_month INTEGER DEFAULT NULL -- NULL = full year
)
RETURNS TABLE (
    account_id UUID,
    account_code VARCHAR(20),
    account_name VARCHAR(255),
    account_type VARCHAR(50),
    cost_center_id UUID,
    cost_center_name VARCHAR(100),
    budget_amount BIGINT,
    actual_amount BIGINT,
    variance BIGINT,
    percentage_used DECIMAL(10,2)
) AS $$
DECLARE
    v_fiscal_year INTEGER;
    v_start_date DATE;
    v_end_date DATE;
BEGIN
    -- Get fiscal year
    SELECT fiscal_year INTO v_fiscal_year FROM budgets WHERE id = p_budget_id;

    -- Calculate date range
    IF p_month IS NULL THEN
        v_start_date := make_date(v_fiscal_year, 1, 1);
        v_end_date := make_date(v_fiscal_year, 12, 31);
    ELSE
        v_start_date := make_date(v_fiscal_year, p_month, 1);
        v_end_date := (v_start_date + INTERVAL '1 month' - INTERVAL '1 day')::DATE;
    END IF;

    RETURN QUERY
    WITH budget_data AS (
        SELECT
            bi.account_id,
            coa.account_code,
            coa.name as account_name,
            coa.account_type,
            bi.cost_center_id,
            cc.name as cost_center_name,
            CASE
                WHEN p_month IS NULL THEN bi.annual_amount
                WHEN p_month = 1 THEN bi.jan_amount
                WHEN p_month = 2 THEN bi.feb_amount
                WHEN p_month = 3 THEN bi.mar_amount
                WHEN p_month = 4 THEN bi.apr_amount
                WHEN p_month = 5 THEN bi.may_amount
                WHEN p_month = 6 THEN bi.jun_amount
                WHEN p_month = 7 THEN bi.jul_amount
                WHEN p_month = 8 THEN bi.aug_amount
                WHEN p_month = 9 THEN bi.sep_amount
                WHEN p_month = 10 THEN bi.oct_amount
                WHEN p_month = 11 THEN bi.nov_amount
                WHEN p_month = 12 THEN bi.dec_amount
                ELSE 0
            END as budget_amt
        FROM budget_items bi
        JOIN chart_of_accounts coa ON bi.account_id = coa.id
        LEFT JOIN cost_centers cc ON bi.cost_center_id = cc.id
        WHERE bi.budget_id = p_budget_id
    ),
    actual_data AS (
        SELECT
            jl.account_id,
            jl.cost_center_id,
            -- For expense accounts: debit - credit
            -- For revenue accounts: credit - debit
            SUM(CASE
                WHEN coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
                THEN jl.debit - jl.credit
                WHEN coa.account_type IN ('REVENUE', 'OTHER_INCOME')
                THEN jl.credit - jl.debit
                ELSE jl.debit - jl.credit
            END) as actual_amt
        FROM journal_lines jl
        JOIN journal_entries je ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON jl.account_id = coa.id
        WHERE je.tenant_id = current_setting('app.tenant_id', true)
        AND je.status = 'POSTED'
        AND je.entry_date BETWEEN v_start_date AND v_end_date
        GROUP BY jl.account_id, jl.cost_center_id
    )
    SELECT
        bd.account_id,
        bd.account_code,
        bd.account_name,
        bd.account_type,
        bd.cost_center_id,
        bd.cost_center_name,
        bd.budget_amt as budget_amount,
        COALESCE(ad.actual_amt, 0)::BIGINT as actual_amount,
        (bd.budget_amt - COALESCE(ad.actual_amt, 0))::BIGINT as variance,
        CASE
            WHEN bd.budget_amt = 0 THEN 0
            ELSE ROUND((COALESCE(ad.actual_amt, 0)::DECIMAL / bd.budget_amt) * 100, 2)
        END as percentage_used
    FROM budget_data bd
    LEFT JOIN actual_data ad ON bd.account_id = ad.account_id
        AND (bd.cost_center_id = ad.cost_center_id OR (bd.cost_center_id IS NULL AND ad.cost_center_id IS NULL))
    ORDER BY bd.account_code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get accounts over budget (variance alerts)
CREATE OR REPLACE FUNCTION get_variance_alerts(
    p_tenant_id TEXT,
    p_threshold_percent DECIMAL DEFAULT 100 -- over 100% = over budget
)
RETURNS TABLE (
    budget_id UUID,
    budget_name VARCHAR(100),
    fiscal_year INTEGER,
    account_id UUID,
    account_code VARCHAR(20),
    account_name VARCHAR(255),
    budget_amount BIGINT,
    actual_amount BIGINT,
    variance BIGINT,
    percentage_used DECIMAL(10,2)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        bva.budget_id,
        bva.budget_name,
        bva.fiscal_year,
        bva.account_id,
        bva.account_code,
        bva.account_name,
        bva.budget_amount,
        bva.actual_amount,
        bva.variance,
        bva.percentage_used
    FROM (
        SELECT
            b.id as budget_id,
            b.name as budget_name,
            b.fiscal_year,
            r.account_id,
            r.account_code,
            r.account_name,
            r.budget_amount,
            r.actual_amount,
            r.variance,
            r.percentage_used
        FROM budgets b
        CROSS JOIN LATERAL get_budget_vs_actual(b.id, NULL) r
        WHERE b.tenant_id = p_tenant_id
        AND b.status = 'active'
    ) bva
    WHERE bva.percentage_used >= p_threshold_percent
    ORDER BY bva.percentage_used DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Budget summary by account type
CREATE OR REPLACE FUNCTION get_budget_summary(p_budget_id UUID)
RETURNS TABLE (
    account_type VARCHAR(50),
    total_budget BIGINT,
    total_actual BIGINT,
    total_variance BIGINT,
    avg_percentage_used DECIMAL(10,2)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.account_type,
        SUM(r.budget_amount)::BIGINT as total_budget,
        SUM(r.actual_amount)::BIGINT as total_actual,
        SUM(r.variance)::BIGINT as total_variance,
        ROUND(AVG(r.percentage_used), 2) as avg_percentage_used
    FROM get_budget_vs_actual(p_budget_id, NULL) r
    GROUP BY r.account_type
    ORDER BY r.account_type;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- Comments
-- =============================================
COMMENT ON TABLE budgets IS 'Budget master - planning data only, NO journal entries';
COMMENT ON TABLE budget_items IS 'Budget line items with monthly breakdown per account';
COMMENT ON COLUMN budget_items.annual_amount IS 'Auto-computed sum of all monthly amounts';
