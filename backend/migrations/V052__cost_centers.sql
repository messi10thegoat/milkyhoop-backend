-- =============================================
-- V052: Cost Centers (Pusat Biaya)
-- Purpose: Track and allocate costs by department/division/project
-- =============================================

-- Cost center master
CREATE TABLE cost_centers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Hierarchy
    parent_id UUID REFERENCES cost_centers(id),
    level INTEGER DEFAULT 1,
    path TEXT, -- materialized path: "parent_code/child_code"

    -- Manager
    manager_name VARCHAR(100),
    manager_email VARCHAR(100),

    -- Status
    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_cost_centers_code UNIQUE(tenant_id, code)
);

-- Add cost_center_id to transaction tables
ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES cost_centers(id);
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES cost_centers(id);
ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES cost_centers(id);

-- Check if expenses table exists before adding column
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'expenses') THEN
        EXECUTE 'ALTER TABLE expenses ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES cost_centers(id)';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'expense_items') THEN
        EXECUTE 'ALTER TABLE expense_items ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES cost_centers(id)';
    END IF;
END $$;

-- RLS
ALTER TABLE cost_centers ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_cost_centers ON cost_centers
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Indexes
CREATE INDEX idx_cost_centers_tenant ON cost_centers(tenant_id);
CREATE INDEX idx_cost_centers_parent ON cost_centers(parent_id);
CREATE INDEX idx_cost_centers_code ON cost_centers(tenant_id, code);
CREATE INDEX idx_cost_centers_active ON cost_centers(tenant_id, is_active) WHERE is_active = true;
CREATE INDEX idx_journal_lines_cost_center ON journal_lines(cost_center_id) WHERE cost_center_id IS NOT NULL;

-- =============================================
-- Helper Functions
-- =============================================

-- Update path when parent changes
CREATE OR REPLACE FUNCTION update_cost_center_path()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.parent_id IS NULL THEN
        NEW.path := NEW.code;
        NEW.level := 1;
    ELSE
        SELECT path || '/' || NEW.code, level + 1
        INTO NEW.path, NEW.level
        FROM cost_centers
        WHERE id = NEW.parent_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cost_center_path
    BEFORE INSERT OR UPDATE OF parent_id, code ON cost_centers
    FOR EACH ROW
    EXECUTE FUNCTION update_cost_center_path();

-- Get cost center tree
CREATE OR REPLACE FUNCTION get_cost_center_tree(p_tenant_id TEXT)
RETURNS TABLE (
    id UUID,
    code VARCHAR(50),
    name VARCHAR(100),
    description TEXT,
    parent_id UUID,
    level INTEGER,
    path TEXT,
    manager_name VARCHAR(100),
    is_active BOOLEAN,
    children_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE cc_tree AS (
        -- Root level
        SELECT
            cc.id, cc.code, cc.name, cc.description, cc.parent_id,
            cc.level, cc.path, cc.manager_name, cc.is_active
        FROM cost_centers cc
        WHERE cc.tenant_id = p_tenant_id AND cc.parent_id IS NULL

        UNION ALL

        -- Children
        SELECT
            cc.id, cc.code, cc.name, cc.description, cc.parent_id,
            cc.level, cc.path, cc.manager_name, cc.is_active
        FROM cost_centers cc
        JOIN cc_tree t ON cc.parent_id = t.id
    )
    SELECT
        t.id, t.code, t.name, t.description, t.parent_id,
        t.level, t.path, t.manager_name, t.is_active,
        (SELECT COUNT(*) FROM cost_centers c WHERE c.parent_id = t.id)::BIGINT as children_count
    FROM cc_tree t
    ORDER BY t.path;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get cost center summary (transactions)
CREATE OR REPLACE FUNCTION get_cost_center_summary(
    p_cost_center_id UUID,
    p_start_date DATE,
    p_end_date DATE
)
RETURNS TABLE (
    account_type VARCHAR(50),
    account_code VARCHAR(20),
    account_name VARCHAR(255),
    total_debit BIGINT,
    total_credit BIGINT,
    net_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        coa.account_type,
        coa.account_code,
        coa.name as account_name,
        COALESCE(SUM(jl.debit), 0)::BIGINT as total_debit,
        COALESCE(SUM(jl.credit), 0)::BIGINT as total_credit,
        COALESCE(SUM(jl.debit - jl.credit), 0)::BIGINT as net_amount
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE jl.cost_center_id = p_cost_center_id
    AND je.status = 'POSTED'
    AND je.entry_date BETWEEN p_start_date AND p_end_date
    GROUP BY coa.account_type, coa.account_code, coa.name
    ORDER BY coa.account_code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Compare all cost centers
CREATE OR REPLACE FUNCTION compare_cost_centers(
    p_tenant_id TEXT,
    p_start_date DATE,
    p_end_date DATE
)
RETURNS TABLE (
    cost_center_id UUID,
    cost_center_code VARCHAR(50),
    cost_center_name VARCHAR(100),
    total_revenue BIGINT,
    total_expense BIGINT,
    net_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        cc.id as cost_center_id,
        cc.code as cost_center_code,
        cc.name as cost_center_name,
        COALESCE(SUM(CASE WHEN coa.account_type IN ('REVENUE', 'OTHER_INCOME')
            THEN jl.credit - jl.debit ELSE 0 END), 0)::BIGINT as total_revenue,
        COALESCE(SUM(CASE WHEN coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
            THEN jl.debit - jl.credit ELSE 0 END), 0)::BIGINT as total_expense,
        COALESCE(SUM(CASE WHEN coa.account_type IN ('REVENUE', 'OTHER_INCOME')
            THEN jl.credit - jl.debit
            WHEN coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
            THEN -(jl.debit - jl.credit)
            ELSE 0 END), 0)::BIGINT as net_amount
    FROM cost_centers cc
    LEFT JOIN journal_lines jl ON cc.id = jl.cost_center_id
    LEFT JOIN journal_entries je ON jl.journal_id = je.id AND je.status = 'POSTED'
        AND je.entry_date BETWEEN p_start_date AND p_end_date
    LEFT JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE cc.tenant_id = p_tenant_id AND cc.is_active = true
    GROUP BY cc.id, cc.code, cc.name
    ORDER BY cc.code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- Comments
-- =============================================
COMMENT ON TABLE cost_centers IS 'Cost centers for tracking expenses by department/division/project';
COMMENT ON COLUMN cost_centers.path IS 'Materialized path for hierarchical queries (parent_code/child_code)';
COMMENT ON COLUMN cost_centers.level IS 'Hierarchy level (1 = root, 2 = child of root, etc)';
