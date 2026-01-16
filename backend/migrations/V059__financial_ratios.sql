-- =============================================
-- V059: Financial Ratios (Rasio Keuangan)
-- Purpose: Calculate and analyze financial ratios for business insights
-- NO JOURNAL ENTRY - This is a calculation/reporting system
-- =============================================

-- ============================================================================
-- FINANCIAL RATIO DEFINITIONS
-- ============================================================================
CREATE TABLE ratio_definitions (
    code VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL, -- liquidity, profitability, efficiency, leverage, valuation

    -- Formula (stored for reference)
    formula TEXT NOT NULL,
    description TEXT,

    -- Interpretation
    ideal_min DECIMAL(15,4),
    ideal_max DECIMAL(15,4),
    higher_is_better BOOLEAN DEFAULT true,

    -- Display
    display_format VARCHAR(20) DEFAULT 'decimal', -- decimal, percentage, times, days
    decimal_places INTEGER DEFAULT 2,

    is_active BOOLEAN DEFAULT true,
    display_order INTEGER DEFAULT 0
);

-- ============================================================================
-- HISTORICAL RATIO SNAPSHOTS
-- ============================================================================
CREATE TABLE ratio_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Period
    snapshot_date DATE NOT NULL,
    period_type VARCHAR(20) NOT NULL, -- daily, monthly, quarterly, yearly
    period_start DATE,
    period_end DATE,

    -- All ratios for this period
    ratios JSONB NOT NULL,

    -- Source data used
    source_data JSONB,

    -- Calculated values (denormalized for quick access)
    current_ratio DECIMAL(15,4),
    quick_ratio DECIMAL(15,4),
    gross_profit_margin DECIMAL(15,4),
    net_profit_margin DECIMAL(15,4),
    debt_to_equity DECIMAL(15,4),

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_ratio_snapshots UNIQUE(tenant_id, snapshot_date, period_type)
);

-- ============================================================================
-- INDUSTRY BENCHMARKS (OPTIONAL)
-- ============================================================================
CREATE TABLE industry_benchmarks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    industry VARCHAR(100) NOT NULL,
    ratio_code VARCHAR(50) NOT NULL REFERENCES ratio_definitions(code),

    benchmark_min DECIMAL(15,4),
    benchmark_avg DECIMAL(15,4),
    benchmark_max DECIMAL(15,4),

    source VARCHAR(255),
    year INTEGER,

    CONSTRAINT uq_industry_benchmarks UNIQUE(industry, ratio_code, year)
);

-- ============================================================================
-- RATIO ALERTS/THRESHOLDS
-- ============================================================================
CREATE TABLE ratio_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    ratio_code VARCHAR(50) NOT NULL REFERENCES ratio_definitions(code),

    -- Alert thresholds
    warning_min DECIMAL(15,4),
    warning_max DECIMAL(15,4),
    critical_min DECIMAL(15,4),
    critical_max DECIMAL(15,4),

    -- Notification
    notify_on_warning BOOLEAN DEFAULT true,
    notify_on_critical BOOLEAN DEFAULT true,

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_ratio_alerts UNIQUE(tenant_id, ratio_code)
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE ratio_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE ratio_alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_ratio_snapshots ON ratio_snapshots
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_ratio_alerts ON ratio_alerts
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX idx_ratio_snapshots_tenant ON ratio_snapshots(tenant_id);
CREATE INDEX idx_ratio_snapshots_date ON ratio_snapshots(tenant_id, snapshot_date DESC);
CREATE INDEX idx_ratio_snapshots_period ON ratio_snapshots(tenant_id, period_type, snapshot_date DESC);
CREATE INDEX idx_ratio_definitions_category ON ratio_definitions(category) WHERE is_active = true;
CREATE INDEX idx_industry_benchmarks_industry ON industry_benchmarks(industry, ratio_code);

-- ============================================================================
-- SEED DEFAULT RATIO DEFINITIONS
-- ============================================================================
INSERT INTO ratio_definitions (code, name, category, formula, description, ideal_min, ideal_max, higher_is_better, display_format, display_order) VALUES
-- Liquidity Ratios
('current_ratio', 'Current Ratio', 'liquidity',
 'Current Assets / Current Liabilities',
 'Measures ability to pay short-term obligations with short-term assets',
 1.5, 3.0, true, 'times', 1),

('quick_ratio', 'Quick Ratio (Acid Test)', 'liquidity',
 '(Current Assets - Inventory) / Current Liabilities',
 'More conservative liquidity measure excluding inventory',
 1.0, 2.0, true, 'times', 2),

('cash_ratio', 'Cash Ratio', 'liquidity',
 'Cash & Cash Equivalents / Current Liabilities',
 'Most conservative liquidity measure using only cash',
 0.2, 0.5, true, 'times', 3),

('working_capital', 'Working Capital', 'liquidity',
 'Current Assets - Current Liabilities',
 'Net working capital available for operations',
 NULL, NULL, true, 'decimal', 4),

-- Profitability Ratios
('gross_profit_margin', 'Gross Profit Margin', 'profitability',
 '(Revenue - COGS) / Revenue × 100',
 'Profit margin after direct costs',
 20, 50, true, 'percentage', 10),

('operating_margin', 'Operating Margin', 'profitability',
 'Operating Income / Revenue × 100',
 'Profit margin from core business operations',
 10, 25, true, 'percentage', 11),

('net_profit_margin', 'Net Profit Margin', 'profitability',
 'Net Income / Revenue × 100',
 'Bottom line profit after all expenses',
 5, 20, true, 'percentage', 12),

('roe', 'Return on Equity (ROE)', 'profitability',
 'Net Income / Shareholders Equity × 100',
 'Return generated on shareholder investment',
 15, 30, true, 'percentage', 13),

('roa', 'Return on Assets (ROA)', 'profitability',
 'Net Income / Total Assets × 100',
 'Efficiency of using assets to generate profit',
 5, 15, true, 'percentage', 14),

-- Efficiency Ratios
('asset_turnover', 'Asset Turnover', 'efficiency',
 'Revenue / Average Total Assets',
 'Revenue generated per rupiah of assets',
 0.5, 2.0, true, 'times', 20),

('inventory_turnover', 'Inventory Turnover', 'efficiency',
 'COGS / Average Inventory',
 'How many times inventory is sold and replaced',
 4, 12, true, 'times', 21),

('days_inventory', 'Days Inventory Outstanding (DIO)', 'efficiency',
 '365 / Inventory Turnover',
 'Average days to sell inventory',
 30, 90, false, 'days', 22),

('receivables_turnover', 'Receivables Turnover', 'efficiency',
 'Revenue / Average Accounts Receivable',
 'How many times receivables are collected',
 6, 12, true, 'times', 23),

('days_receivable', 'Days Sales Outstanding (DSO)', 'efficiency',
 '365 / Receivables Turnover',
 'Average days to collect receivables',
 30, 60, false, 'days', 24),

('payables_turnover', 'Payables Turnover', 'efficiency',
 'COGS / Average Accounts Payable',
 'How many times payables are paid',
 6, 12, NULL, 'times', 25),

('days_payable', 'Days Payable Outstanding (DPO)', 'efficiency',
 '365 / Payables Turnover',
 'Average days to pay suppliers',
 30, 60, NULL, 'days', 26),

('cash_conversion_cycle', 'Cash Conversion Cycle (CCC)', 'efficiency',
 'Days Inventory + Days Receivable - Days Payable',
 'Days between paying suppliers and collecting from customers',
 NULL, 90, false, 'days', 27),

-- Leverage Ratios
('debt_ratio', 'Debt Ratio', 'leverage',
 'Total Liabilities / Total Assets × 100',
 'Percentage of assets financed by debt',
 20, 60, false, 'percentage', 30),

('debt_to_equity', 'Debt to Equity Ratio', 'leverage',
 'Total Liabilities / Shareholders Equity',
 'Proportion of debt relative to equity',
 0.5, 2.0, false, 'times', 31),

('interest_coverage', 'Interest Coverage Ratio', 'leverage',
 'EBIT / Interest Expense',
 'Ability to pay interest from operating income',
 3, 10, true, 'times', 32),

('equity_ratio', 'Equity Ratio', 'leverage',
 'Shareholders Equity / Total Assets × 100',
 'Percentage of assets financed by equity',
 40, 80, true, 'percentage', 33)

ON CONFLICT (code) DO NOTHING;

-- ============================================================================
-- FUNCTION: CALCULATE FINANCIAL RATIOS
-- ============================================================================
CREATE OR REPLACE FUNCTION calculate_financial_ratios(
    p_tenant_id TEXT,
    p_as_of_date DATE,
    p_period_start DATE DEFAULT NULL,
    p_period_end DATE DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    v_result JSONB;
    v_current_assets BIGINT := 0;
    v_current_liabilities BIGINT := 0;
    v_total_assets BIGINT := 0;
    v_total_liabilities BIGINT := 0;
    v_equity BIGINT := 0;
    v_inventory BIGINT := 0;
    v_cash BIGINT := 0;
    v_receivables BIGINT := 0;
    v_payables BIGINT := 0;
    v_revenue BIGINT := 0;
    v_cogs BIGINT := 0;
    v_operating_income BIGINT := 0;
    v_net_income BIGINT := 0;
    v_interest_expense BIGINT := 0;
    v_ratios JSONB := '{}'::JSONB;
    v_source_data JSONB;
BEGIN
    -- Set default period if not provided
    IF p_period_start IS NULL THEN
        p_period_start := date_trunc('year', p_as_of_date)::DATE;
    END IF;
    IF p_period_end IS NULL THEN
        p_period_end := p_as_of_date;
    END IF;

    -- Get balance sheet figures from chart_of_accounts balances
    -- Current Assets (account codes starting with 1-10, 1-11, 1-12, 1-13)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_current_assets
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'ASSET'
    AND (coa.account_code LIKE '1-10%' OR coa.account_code LIKE '1-11%'
         OR coa.account_code LIKE '1-12%' OR coa.account_code LIKE '1-13%');

    -- Cash specifically (1-10100, 1-10200)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_cash
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code IN ('1-10100', '1-10200');

    -- Inventory (1-10400)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_inventory
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code LIKE '1-104%';

    -- Receivables (1-10300)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_receivables
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code LIKE '1-103%';

    -- Total Assets
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_total_assets
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'ASSET';

    -- Current Liabilities (2-10%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_current_liabilities
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'LIABILITY'
    AND coa.account_code LIKE '2-10%';

    -- Payables (2-10100)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_payables
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code = '2-10100';

    -- Total Liabilities
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_total_liabilities
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'LIABILITY';

    -- Equity (3-%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_equity
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'EQUITY';

    -- Revenue for period (4-%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_revenue
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_period_start AND p_period_end
    AND coa.account_type = 'REVENUE';

    -- COGS for period (5-10%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_cogs
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_period_start AND p_period_end
    AND coa.account_code LIKE '5-10%';

    -- Operating expenses for period (5-20%, 5-30%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_operating_income
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_period_start AND p_period_end
    AND coa.account_type = 'EXPENSE';

    -- Calculate net income
    v_net_income := v_revenue - v_cogs - v_operating_income;
    v_operating_income := v_revenue - v_cogs - v_operating_income;

    -- Store source data
    v_source_data := jsonb_build_object(
        'current_assets', v_current_assets,
        'current_liabilities', v_current_liabilities,
        'total_assets', v_total_assets,
        'total_liabilities', v_total_liabilities,
        'equity', v_equity,
        'inventory', v_inventory,
        'cash', v_cash,
        'receivables', v_receivables,
        'payables', v_payables,
        'revenue', v_revenue,
        'cogs', v_cogs,
        'operating_income', v_operating_income,
        'net_income', v_net_income
    );

    -- Calculate ratios (avoiding division by zero)
    v_ratios := jsonb_build_object(
        'liquidity', jsonb_build_object(
            'current_ratio', CASE WHEN v_current_liabilities > 0
                THEN ROUND((v_current_assets::NUMERIC / v_current_liabilities), 4) ELSE NULL END,
            'quick_ratio', CASE WHEN v_current_liabilities > 0
                THEN ROUND(((v_current_assets - v_inventory)::NUMERIC / v_current_liabilities), 4) ELSE NULL END,
            'cash_ratio', CASE WHEN v_current_liabilities > 0
                THEN ROUND((v_cash::NUMERIC / v_current_liabilities), 4) ELSE NULL END,
            'working_capital', v_current_assets - v_current_liabilities
        ),
        'profitability', jsonb_build_object(
            'gross_profit_margin', CASE WHEN v_revenue > 0
                THEN ROUND(((v_revenue - v_cogs)::NUMERIC / v_revenue * 100), 2) ELSE NULL END,
            'net_profit_margin', CASE WHEN v_revenue > 0
                THEN ROUND((v_net_income::NUMERIC / v_revenue * 100), 2) ELSE NULL END,
            'roe', CASE WHEN v_equity > 0
                THEN ROUND((v_net_income::NUMERIC / v_equity * 100), 2) ELSE NULL END,
            'roa', CASE WHEN v_total_assets > 0
                THEN ROUND((v_net_income::NUMERIC / v_total_assets * 100), 2) ELSE NULL END
        ),
        'efficiency', jsonb_build_object(
            'asset_turnover', CASE WHEN v_total_assets > 0
                THEN ROUND((v_revenue::NUMERIC / v_total_assets), 4) ELSE NULL END,
            'inventory_turnover', CASE WHEN v_inventory > 0
                THEN ROUND((v_cogs::NUMERIC / v_inventory), 4) ELSE NULL END,
            'days_inventory', CASE WHEN v_cogs > 0 AND v_inventory > 0
                THEN ROUND((365.0 * v_inventory / v_cogs), 0) ELSE NULL END,
            'receivables_turnover', CASE WHEN v_receivables > 0
                THEN ROUND((v_revenue::NUMERIC / v_receivables), 4) ELSE NULL END,
            'days_receivable', CASE WHEN v_revenue > 0 AND v_receivables > 0
                THEN ROUND((365.0 * v_receivables / v_revenue), 0) ELSE NULL END,
            'payables_turnover', CASE WHEN v_payables > 0
                THEN ROUND((v_cogs::NUMERIC / v_payables), 4) ELSE NULL END,
            'days_payable', CASE WHEN v_cogs > 0 AND v_payables > 0
                THEN ROUND((365.0 * v_payables / v_cogs), 0) ELSE NULL END
        ),
        'leverage', jsonb_build_object(
            'debt_ratio', CASE WHEN v_total_assets > 0
                THEN ROUND((v_total_liabilities::NUMERIC / v_total_assets * 100), 2) ELSE NULL END,
            'debt_to_equity', CASE WHEN v_equity > 0
                THEN ROUND((v_total_liabilities::NUMERIC / v_equity), 4) ELSE NULL END,
            'equity_ratio', CASE WHEN v_total_assets > 0
                THEN ROUND((v_equity::NUMERIC / v_total_assets * 100), 2) ELSE NULL END
        )
    );

    -- Build result
    v_result := jsonb_build_object(
        'calculated_at', NOW(),
        'as_of_date', p_as_of_date,
        'period_start', p_period_start,
        'period_end', p_period_end,
        'ratios', v_ratios,
        'source_data', v_source_data
    );

    RETURN v_result;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: SAVE RATIO SNAPSHOT
-- ============================================================================
CREATE OR REPLACE FUNCTION save_ratio_snapshot(
    p_tenant_id TEXT,
    p_snapshot_date DATE,
    p_period_type VARCHAR(20) DEFAULT 'monthly'
) RETURNS UUID AS $$
DECLARE
    v_snapshot_id UUID;
    v_period_start DATE;
    v_period_end DATE;
    v_ratios JSONB;
BEGIN
    -- Determine period
    CASE p_period_type
        WHEN 'daily' THEN
            v_period_start := p_snapshot_date;
            v_period_end := p_snapshot_date;
        WHEN 'monthly' THEN
            v_period_start := date_trunc('month', p_snapshot_date)::DATE;
            v_period_end := (date_trunc('month', p_snapshot_date) + INTERVAL '1 month - 1 day')::DATE;
        WHEN 'quarterly' THEN
            v_period_start := date_trunc('quarter', p_snapshot_date)::DATE;
            v_period_end := (date_trunc('quarter', p_snapshot_date) + INTERVAL '3 months - 1 day')::DATE;
        WHEN 'yearly' THEN
            v_period_start := date_trunc('year', p_snapshot_date)::DATE;
            v_period_end := (date_trunc('year', p_snapshot_date) + INTERVAL '1 year - 1 day')::DATE;
        ELSE
            v_period_start := date_trunc('month', p_snapshot_date)::DATE;
            v_period_end := p_snapshot_date;
    END CASE;

    -- Calculate ratios
    v_ratios := calculate_financial_ratios(p_tenant_id, p_snapshot_date, v_period_start, v_period_end);

    -- Insert or update snapshot
    INSERT INTO ratio_snapshots (
        tenant_id, snapshot_date, period_type, period_start, period_end,
        ratios, source_data,
        current_ratio, quick_ratio, gross_profit_margin, net_profit_margin, debt_to_equity
    ) VALUES (
        p_tenant_id, p_snapshot_date, p_period_type, v_period_start, v_period_end,
        v_ratios->'ratios', v_ratios->'source_data',
        (v_ratios->'ratios'->'liquidity'->>'current_ratio')::DECIMAL,
        (v_ratios->'ratios'->'liquidity'->>'quick_ratio')::DECIMAL,
        (v_ratios->'ratios'->'profitability'->>'gross_profit_margin')::DECIMAL,
        (v_ratios->'ratios'->'profitability'->>'net_profit_margin')::DECIMAL,
        (v_ratios->'ratios'->'leverage'->>'debt_to_equity')::DECIMAL
    )
    ON CONFLICT (tenant_id, snapshot_date, period_type)
    DO UPDATE SET
        period_start = EXCLUDED.period_start,
        period_end = EXCLUDED.period_end,
        ratios = EXCLUDED.ratios,
        source_data = EXCLUDED.source_data,
        current_ratio = EXCLUDED.current_ratio,
        quick_ratio = EXCLUDED.quick_ratio,
        gross_profit_margin = EXCLUDED.gross_profit_margin,
        net_profit_margin = EXCLUDED.net_profit_margin,
        debt_to_equity = EXCLUDED.debt_to_equity,
        created_at = NOW()
    RETURNING id INTO v_snapshot_id;

    RETURN v_snapshot_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: GET RATIO TREND
-- ============================================================================
CREATE OR REPLACE FUNCTION get_ratio_trend(
    p_tenant_id TEXT,
    p_ratio_code VARCHAR(50),
    p_periods INTEGER DEFAULT 12,
    p_period_type VARCHAR(20) DEFAULT 'monthly'
) RETURNS TABLE (
    snapshot_date DATE,
    value DECIMAL(15,4)
) AS $$
DECLARE
    v_ratio_path TEXT[];
BEGIN
    -- Determine JSON path based on ratio code
    CASE p_ratio_code
        WHEN 'current_ratio' THEN v_ratio_path := ARRAY['liquidity', 'current_ratio'];
        WHEN 'quick_ratio' THEN v_ratio_path := ARRAY['liquidity', 'quick_ratio'];
        WHEN 'cash_ratio' THEN v_ratio_path := ARRAY['liquidity', 'cash_ratio'];
        WHEN 'working_capital' THEN v_ratio_path := ARRAY['liquidity', 'working_capital'];
        WHEN 'gross_profit_margin' THEN v_ratio_path := ARRAY['profitability', 'gross_profit_margin'];
        WHEN 'net_profit_margin' THEN v_ratio_path := ARRAY['profitability', 'net_profit_margin'];
        WHEN 'roe' THEN v_ratio_path := ARRAY['profitability', 'roe'];
        WHEN 'roa' THEN v_ratio_path := ARRAY['profitability', 'roa'];
        WHEN 'debt_ratio' THEN v_ratio_path := ARRAY['leverage', 'debt_ratio'];
        WHEN 'debt_to_equity' THEN v_ratio_path := ARRAY['leverage', 'debt_to_equity'];
        WHEN 'inventory_turnover' THEN v_ratio_path := ARRAY['efficiency', 'inventory_turnover'];
        WHEN 'days_receivable' THEN v_ratio_path := ARRAY['efficiency', 'days_receivable'];
        ELSE v_ratio_path := ARRAY['liquidity', p_ratio_code];
    END CASE;

    RETURN QUERY
    SELECT
        rs.snapshot_date,
        (rs.ratios->v_ratio_path[1]->>v_ratio_path[2])::DECIMAL(15,4) as value
    FROM ratio_snapshots rs
    WHERE rs.tenant_id = p_tenant_id
    AND rs.period_type = p_period_type
    ORDER BY rs.snapshot_date DESC
    LIMIT p_periods;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: GET RATIO ALERTS
-- ============================================================================
CREATE OR REPLACE FUNCTION check_ratio_alerts(p_tenant_id TEXT)
RETURNS TABLE (
    ratio_code VARCHAR(50),
    ratio_name VARCHAR(100),
    current_value DECIMAL(15,4),
    alert_level VARCHAR(20),
    threshold_min DECIMAL(15,4),
    threshold_max DECIMAL(15,4)
) AS $$
BEGIN
    RETURN QUERY
    WITH latest_ratios AS (
        SELECT ratios
        FROM ratio_snapshots
        WHERE tenant_id = p_tenant_id
        ORDER BY snapshot_date DESC
        LIMIT 1
    ),
    flattened AS (
        SELECT
            rd.code,
            rd.name,
            CASE rd.code
                WHEN 'current_ratio' THEN (lr.ratios->'liquidity'->>'current_ratio')::DECIMAL
                WHEN 'quick_ratio' THEN (lr.ratios->'liquidity'->>'quick_ratio')::DECIMAL
                WHEN 'gross_profit_margin' THEN (lr.ratios->'profitability'->>'gross_profit_margin')::DECIMAL
                WHEN 'net_profit_margin' THEN (lr.ratios->'profitability'->>'net_profit_margin')::DECIMAL
                WHEN 'debt_to_equity' THEN (lr.ratios->'leverage'->>'debt_to_equity')::DECIMAL
                ELSE NULL
            END as value
        FROM ratio_definitions rd
        CROSS JOIN latest_ratios lr
        WHERE rd.is_active = true
    )
    SELECT
        f.code,
        f.name,
        f.value,
        CASE
            WHEN ra.critical_min IS NOT NULL AND f.value < ra.critical_min THEN 'critical'
            WHEN ra.critical_max IS NOT NULL AND f.value > ra.critical_max THEN 'critical'
            WHEN ra.warning_min IS NOT NULL AND f.value < ra.warning_min THEN 'warning'
            WHEN ra.warning_max IS NOT NULL AND f.value > ra.warning_max THEN 'warning'
            WHEN rd.ideal_min IS NOT NULL AND f.value < rd.ideal_min THEN 'below_ideal'
            WHEN rd.ideal_max IS NOT NULL AND f.value > rd.ideal_max THEN 'above_ideal'
            ELSE 'normal'
        END as alert_level,
        COALESCE(ra.warning_min, rd.ideal_min),
        COALESCE(ra.warning_max, rd.ideal_max)
    FROM flattened f
    JOIN ratio_definitions rd ON f.code = rd.code
    LEFT JOIN ratio_alerts ra ON ra.ratio_code = f.code AND ra.tenant_id = p_tenant_id
    WHERE f.value IS NOT NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- COMMENTS
-- ============================================================================
COMMENT ON TABLE ratio_definitions IS 'Standard financial ratio definitions with formulas and benchmarks';
COMMENT ON TABLE ratio_snapshots IS 'Historical snapshots of calculated ratios for trend analysis';
COMMENT ON TABLE industry_benchmarks IS 'Industry-specific benchmarks for ratio comparison';
COMMENT ON TABLE ratio_alerts IS 'Tenant-configurable alert thresholds for ratios';
COMMENT ON FUNCTION calculate_financial_ratios IS 'Calculate all financial ratios for a given date';
COMMENT ON FUNCTION save_ratio_snapshot IS 'Save calculated ratios as a historical snapshot';
COMMENT ON FUNCTION get_ratio_trend IS 'Get historical trend for a specific ratio';
