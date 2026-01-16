-- =============================================
-- V063: Bill of Materials (BOM)
-- Purpose: Define product structure/recipe for manufacturing
-- =============================================

-- ============================================================================
-- 1. WORK CENTERS
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_centers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Location
    warehouse_id UUID REFERENCES warehouses(id),

    -- Capacity
    capacity_per_hour DECIMAL(15,4),
    hours_per_day DECIMAL(4,2) DEFAULT 8,

    -- Rates
    labor_rate_per_hour BIGINT DEFAULT 0,
    overhead_rate_per_hour BIGINT DEFAULT 0,

    -- Status
    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_work_centers UNIQUE(tenant_id, code)
);

-- ============================================================================
-- 2. BILL OF MATERIALS
-- ============================================================================

CREATE TABLE IF NOT EXISTS bill_of_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Product being manufactured
    product_id UUID NOT NULL REFERENCES products(id),

    -- BOM info
    bom_code VARCHAR(50) NOT NULL,
    bom_name VARCHAR(255),
    description TEXT,

    -- Version control
    version INTEGER DEFAULT 1,
    is_current BOOLEAN DEFAULT true,
    effective_date DATE,
    obsolete_date DATE,

    -- Quantity
    output_quantity DECIMAL(15,4) DEFAULT 1,
    output_unit VARCHAR(50),

    -- Costing
    standard_cost BIGINT DEFAULT 0, -- calculated from components
    labor_cost BIGINT DEFAULT 0,
    overhead_cost BIGINT DEFAULT 0,
    total_cost BIGINT DEFAULT 0, -- standard_cost + labor + overhead

    -- Production
    estimated_time_minutes INTEGER,
    work_center_id UUID REFERENCES work_centers(id),

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, active, obsolete

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_bom UNIQUE(tenant_id, bom_code),
    CONSTRAINT chk_bom_status CHECK (status IN ('draft', 'active', 'obsolete'))
);

-- ============================================================================
-- 3. BOM OPERATIONS (Work Instructions/Steps)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bom_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_id UUID NOT NULL REFERENCES bill_of_materials(id) ON DELETE CASCADE,

    operation_number INTEGER NOT NULL, -- 10, 20, 30...
    operation_name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Work center
    work_center_id UUID REFERENCES work_centers(id),

    -- Time
    setup_time_minutes INTEGER DEFAULT 0,
    run_time_minutes INTEGER, -- per unit

    -- Cost
    labor_rate_per_hour BIGINT DEFAULT 0,
    overhead_rate_per_hour BIGINT DEFAULT 0,

    -- Instructions
    instructions TEXT,

    CONSTRAINT uq_bom_operations UNIQUE(bom_id, operation_number)
);

-- ============================================================================
-- 4. BOM COMPONENTS (Materials Needed)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bom_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_id UUID NOT NULL REFERENCES bill_of_materials(id) ON DELETE CASCADE,

    -- Component
    component_product_id UUID NOT NULL REFERENCES products(id),

    -- Quantity
    quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(50),

    -- Wastage/shrinkage allowance
    wastage_percent DECIMAL(5,2) DEFAULT 0,

    -- Sequence
    sequence_order INTEGER DEFAULT 0,

    -- Operation (which step uses this)
    operation_id UUID REFERENCES bom_operations(id),

    -- Cost
    unit_cost BIGINT DEFAULT 0, -- from component's purchase price or standard cost
    extended_cost BIGINT DEFAULT 0, -- quantity * unit_cost * (1 + wastage)

    -- Notes
    notes TEXT,

    -- For substitute components
    is_substitute BOOLEAN DEFAULT false,
    substitute_for_id UUID REFERENCES bom_components(id)
);

-- ============================================================================
-- 5. BOM COMPONENT SUBSTITUTES (Alternative Materials)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bom_substitutes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_component_id UUID NOT NULL REFERENCES bom_components(id) ON DELETE CASCADE,

    substitute_product_id UUID NOT NULL REFERENCES products(id),
    quantity_ratio DECIMAL(10,4) DEFAULT 1.0, -- ratio compared to original
    priority INTEGER DEFAULT 1, -- 1 = first choice substitute

    notes TEXT,

    CONSTRAINT uq_bom_substitutes UNIQUE(bom_component_id, substitute_product_id)
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE work_centers ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_of_materials ENABLE ROW LEVEL SECURITY;
ALTER TABLE bom_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bom_components ENABLE ROW LEVEL SECURITY;
ALTER TABLE bom_substitutes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_work_centers ON work_centers;
CREATE POLICY rls_work_centers ON work_centers
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_bill_of_materials ON bill_of_materials;
CREATE POLICY rls_bill_of_materials ON bill_of_materials
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_bom_operations ON bom_operations;
CREATE POLICY rls_bom_operations ON bom_operations
    USING (bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_bom_components ON bom_components;
CREATE POLICY rls_bom_components ON bom_components
    USING (bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_bom_substitutes ON bom_substitutes;
CREATE POLICY rls_bom_substitutes ON bom_substitutes
    USING (bom_component_id IN (
        SELECT bc.id FROM bom_components bc
        JOIN bill_of_materials bom ON bom.id = bc.bom_id
        WHERE bom.tenant_id = current_setting('app.tenant_id', true)
    ));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_work_centers_tenant ON work_centers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_work_centers_active ON work_centers(tenant_id, is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_bom_product ON bill_of_materials(product_id);
CREATE INDEX IF NOT EXISTS idx_bom_status ON bill_of_materials(tenant_id, status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_bom_current ON bill_of_materials(tenant_id, product_id, is_current) WHERE is_current = true;
CREATE INDEX IF NOT EXISTS idx_bom_components_bom ON bom_components(bom_id);
CREATE INDEX IF NOT EXISTS idx_bom_components_product ON bom_components(component_product_id);
CREATE INDEX IF NOT EXISTS idx_bom_operations_bom ON bom_operations(bom_id);

-- ============================================================================
-- FUNCTION: Calculate BOM Cost
-- ============================================================================

CREATE OR REPLACE FUNCTION calculate_bom_cost(p_bom_id UUID)
RETURNS BIGINT AS $$
DECLARE
    v_material_cost BIGINT;
    v_labor_cost BIGINT;
    v_overhead_cost BIGINT;
BEGIN
    -- Sum component costs
    SELECT COALESCE(SUM(extended_cost), 0)
    INTO v_material_cost
    FROM bom_components
    WHERE bom_id = p_bom_id;

    -- Calculate labor and overhead from operations
    SELECT
        COALESCE(SUM((COALESCE(setup_time_minutes, 0) + COALESCE(run_time_minutes, 0)) * labor_rate_per_hour / 60), 0),
        COALESCE(SUM((COALESCE(setup_time_minutes, 0) + COALESCE(run_time_minutes, 0)) * overhead_rate_per_hour / 60), 0)
    INTO v_labor_cost, v_overhead_cost
    FROM bom_operations
    WHERE bom_id = p_bom_id;

    -- Update BOM
    UPDATE bill_of_materials
    SET standard_cost = v_material_cost,
        labor_cost = v_labor_cost,
        overhead_cost = v_overhead_cost,
        total_cost = v_material_cost + v_labor_cost + v_overhead_cost,
        updated_at = NOW()
    WHERE id = p_bom_id;

    RETURN v_material_cost + v_labor_cost + v_overhead_cost;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: Update Component Extended Cost
-- ============================================================================

CREATE OR REPLACE FUNCTION update_bom_component_cost()
RETURNS TRIGGER AS $$
BEGIN
    -- Calculate extended cost including wastage
    NEW.extended_cost := ROUND(
        NEW.quantity * NEW.unit_cost * (1 + COALESCE(NEW.wastage_percent, 0) / 100)
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_bom_component_cost ON bom_components;
CREATE TRIGGER trg_update_bom_component_cost
BEFORE INSERT OR UPDATE OF quantity, unit_cost, wastage_percent ON bom_components
FOR EACH ROW
EXECUTE FUNCTION update_bom_component_cost();

-- ============================================================================
-- NOTE: No journal entries - BOM is master data/recipe
-- Journal entries occur during Production Order execution
-- ============================================================================
