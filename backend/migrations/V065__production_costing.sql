-- =============================================
-- V065: Production Costing (Kalkulasi Harga Produksi)
-- Purpose: Calculate actual production costs and analyze variances
-- =============================================

-- ============================================================================
-- 1. STANDARD COSTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS standard_costs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    product_id UUID NOT NULL REFERENCES products(id),

    -- Period
    effective_date DATE NOT NULL,
    end_date DATE,

    -- Costs
    material_cost BIGINT NOT NULL,
    labor_cost BIGINT NOT NULL,
    overhead_cost BIGINT NOT NULL,
    total_cost BIGINT NOT NULL,

    -- Breakdown
    cost_breakdown JSONB,

    -- Source
    source VARCHAR(50), -- bom_calculation, actual_average, manual
    bom_id UUID REFERENCES bill_of_materials(id),

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_standard_costs UNIQUE(tenant_id, product_id, effective_date)
);

-- ============================================================================
-- 2. COST VARIANCES
-- ============================================================================

CREATE TABLE IF NOT EXISTS cost_variances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Reference
    production_order_id UUID REFERENCES production_orders(id),
    product_id UUID NOT NULL REFERENCES products(id),

    -- Period
    period_year INTEGER NOT NULL,
    period_month INTEGER NOT NULL,
    analysis_date DATE NOT NULL,

    -- Quantity
    produced_quantity DECIMAL(15,4) NOT NULL,

    -- Standard costs (expected)
    standard_material_cost BIGINT DEFAULT 0,
    standard_labor_cost BIGINT DEFAULT 0,
    standard_overhead_cost BIGINT DEFAULT 0,
    standard_total_cost BIGINT DEFAULT 0,

    -- Actual costs
    actual_material_cost BIGINT DEFAULT 0,
    actual_labor_cost BIGINT DEFAULT 0,
    actual_overhead_cost BIGINT DEFAULT 0,
    actual_total_cost BIGINT DEFAULT 0,

    -- Variances
    material_variance BIGINT DEFAULT 0,
    labor_variance BIGINT DEFAULT 0,
    overhead_variance BIGINT DEFAULT 0,
    total_variance BIGINT DEFAULT 0,

    -- Detailed variance breakdown
    material_price_variance BIGINT DEFAULT 0, -- (actual price - std price) × actual qty
    material_usage_variance BIGINT DEFAULT 0, -- (actual qty - std qty) × std price
    labor_rate_variance BIGINT DEFAULT 0,
    labor_efficiency_variance BIGINT DEFAULT 0,
    overhead_spending_variance BIGINT DEFAULT 0,
    overhead_efficiency_variance BIGINT DEFAULT 0,

    -- Journal
    variance_journal_id UUID REFERENCES journal_entries(id),

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, posted

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_variance_status CHECK (status IN ('draft', 'posted'))
);

-- ============================================================================
-- 3. COST POOLS (For Overhead Allocation)
-- ============================================================================

CREATE TABLE IF NOT EXISTS cost_pools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Pool type
    pool_type VARCHAR(50), -- manufacturing_overhead, indirect_labor, utilities, depreciation

    -- Allocation basis
    allocation_basis VARCHAR(50), -- direct_labor_hours, machine_hours, units_produced, material_cost

    -- Budget
    budgeted_amount BIGINT DEFAULT 0,
    budgeted_basis_quantity DECIMAL(15,4) DEFAULT 0,
    rate_per_unit BIGINT DEFAULT 0,

    -- Actual
    actual_amount BIGINT DEFAULT 0,
    actual_basis_quantity DECIMAL(15,4) DEFAULT 0,

    -- Period
    fiscal_year INTEGER,

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_cost_pools UNIQUE(tenant_id, code, fiscal_year)
);

-- ============================================================================
-- 4. OVERHEAD ALLOCATIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS overhead_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    cost_pool_id UUID NOT NULL REFERENCES cost_pools(id),
    production_order_id UUID NOT NULL REFERENCES production_orders(id),

    allocation_date DATE NOT NULL,

    -- Basis
    basis_quantity DECIMAL(15,4) NOT NULL,
    rate_per_unit BIGINT NOT NULL,

    -- Allocated amount
    allocated_amount BIGINT NOT NULL,

    -- Journal
    journal_id UUID REFERENCES journal_entries(id),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- VARIANCE ACCOUNTS (Seed if not exist)
-- 5-10170 Selisih Harga Bahan (Material Price Variance)
-- 5-10171 Selisih Pemakaian Bahan (Material Usage Variance)
-- 5-10172 Selisih Tarif Upah (Labor Rate Variance)
-- 5-10173 Selisih Efisiensi Upah (Labor Efficiency Variance)
-- 5-10174 Selisih Overhead (Overhead Variance)
-- ============================================================================

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE standard_costs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_variances ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_pools ENABLE ROW LEVEL SECURITY;
ALTER TABLE overhead_allocations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_standard_costs ON standard_costs;
CREATE POLICY rls_standard_costs ON standard_costs
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_cost_variances ON cost_variances;
CREATE POLICY rls_cost_variances ON cost_variances
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_cost_pools ON cost_pools;
CREATE POLICY rls_cost_pools ON cost_pools
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_overhead_allocations ON overhead_allocations;
CREATE POLICY rls_overhead_allocations ON overhead_allocations
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_standard_costs_product ON standard_costs(product_id, effective_date DESC);
CREATE INDEX IF NOT EXISTS idx_standard_costs_tenant ON standard_costs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cost_variances_period ON cost_variances(tenant_id, period_year, period_month);
CREATE INDEX IF NOT EXISTS idx_cost_variances_product ON cost_variances(product_id);
CREATE INDEX IF NOT EXISTS idx_cost_pools_tenant ON cost_pools(tenant_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_overhead_allocations_order ON overhead_allocations(production_order_id);

-- ============================================================================
-- FUNCTION: Get Standard Cost for Date
-- ============================================================================

CREATE OR REPLACE FUNCTION get_standard_cost(
    p_product_id UUID,
    p_as_of_date DATE
) RETURNS BIGINT AS $$
DECLARE
    v_cost BIGINT;
BEGIN
    SELECT total_cost INTO v_cost
    FROM standard_costs
    WHERE product_id = p_product_id
    AND effective_date <= p_as_of_date
    AND (end_date IS NULL OR end_date >= p_as_of_date)
    ORDER BY effective_date DESC
    LIMIT 1;

    RETURN COALESCE(v_cost, 0);
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- JOURNAL ENTRIES:
-- Material Price Variance (Unfavorable): Dr. Variance / Cr. WIP
-- Material Price Variance (Favorable): Dr. WIP / Cr. Variance
-- Labor Rate Variance: Similar pattern
-- ============================================================================
