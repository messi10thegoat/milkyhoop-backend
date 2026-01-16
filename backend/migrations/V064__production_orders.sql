-- =============================================
-- V064: Production Orders (Perintah Produksi / Work Orders)
-- Purpose: Execute manufacturing based on BOM with material and labor tracking
-- =============================================

-- ============================================================================
-- 1. PRODUCTION ORDERS
-- ============================================================================

CREATE TABLE IF NOT EXISTS production_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Order info
    order_number VARCHAR(50) NOT NULL,
    order_date DATE NOT NULL,

    -- What to produce
    product_id UUID NOT NULL REFERENCES products(id),
    bom_id UUID NOT NULL REFERENCES bill_of_materials(id),

    -- Quantity
    planned_quantity DECIMAL(15,4) NOT NULL,
    completed_quantity DECIMAL(15,4) DEFAULT 0,
    scrapped_quantity DECIMAL(15,4) DEFAULT 0,
    unit VARCHAR(50),

    -- Scheduling
    planned_start_date DATE,
    planned_end_date DATE,
    actual_start_date DATE,
    actual_end_date DATE,

    -- Work center
    work_center_id UUID REFERENCES work_centers(id),
    warehouse_id UUID REFERENCES warehouses(id), -- for finished goods

    -- Reference
    sales_order_id UUID REFERENCES sales_orders(id),
    customer_id UUID,

    -- Costing
    planned_material_cost BIGINT DEFAULT 0,
    planned_labor_cost BIGINT DEFAULT 0,
    planned_overhead_cost BIGINT DEFAULT 0,
    actual_material_cost BIGINT DEFAULT 0,
    actual_labor_cost BIGINT DEFAULT 0,
    actual_overhead_cost BIGINT DEFAULT 0,
    variance_amount BIGINT DEFAULT 0, -- actual - planned

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, planned, released, in_progress, completed, cancelled
    priority INTEGER DEFAULT 5, -- 1=highest, 10=lowest

    -- Journals
    material_issue_journal_id UUID REFERENCES journal_entries(id),
    labor_journal_id UUID REFERENCES journal_entries(id),
    completion_journal_id UUID REFERENCES journal_entries(id),

    -- Notes
    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_production_orders UNIQUE(tenant_id, order_number),
    CONSTRAINT chk_po_status CHECK (status IN ('draft', 'planned', 'released', 'in_progress', 'completed', 'cancelled'))
);

-- ============================================================================
-- 2. PRODUCTION ORDER MATERIALS
-- ============================================================================

CREATE TABLE IF NOT EXISTS production_order_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    production_order_id UUID NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE,

    -- Material
    product_id UUID NOT NULL REFERENCES products(id),

    -- Planned (from BOM)
    planned_quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(50),
    planned_cost BIGINT DEFAULT 0,

    -- Actual issued
    issued_quantity DECIMAL(15,4) DEFAULT 0,
    actual_cost BIGINT DEFAULT 0,

    -- Returned (unused)
    returned_quantity DECIMAL(15,4) DEFAULT 0,

    -- Variance
    variance_quantity DECIMAL(15,4) DEFAULT 0,
    variance_cost BIGINT DEFAULT 0,

    -- Batch/Serial tracking
    batch_id UUID REFERENCES item_batches(id),
    serial_ids UUID[],

    -- Issue tracking
    issued_date DATE,
    issued_by UUID,
    warehouse_id UUID REFERENCES warehouses(id)
);

-- ============================================================================
-- 3. PRODUCTION ORDER LABOR
-- ============================================================================

CREATE TABLE IF NOT EXISTS production_order_labor (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    production_order_id UUID NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE,

    -- Operation
    operation_id UUID REFERENCES bom_operations(id),
    operation_name VARCHAR(100),

    -- Planned
    planned_hours DECIMAL(10,2) DEFAULT 0,
    planned_cost BIGINT DEFAULT 0,

    -- Actual
    actual_hours DECIMAL(10,2) DEFAULT 0,
    actual_cost BIGINT DEFAULT 0,

    -- Worker info
    worker_id UUID,
    worker_name VARCHAR(100),

    -- Time tracking
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,

    -- Rate
    hourly_rate BIGINT DEFAULT 0,

    -- Notes
    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 4. PRODUCTION COMPLETIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS production_completions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    production_order_id UUID NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE,

    completion_date DATE NOT NULL,

    -- Quantities
    good_quantity DECIMAL(15,4) NOT NULL,
    scrap_quantity DECIMAL(15,4) DEFAULT 0,

    -- Quality
    quality_status VARCHAR(20) DEFAULT 'passed', -- passed, failed, rework
    inspection_notes TEXT,

    -- Cost allocation
    unit_cost BIGINT DEFAULT 0,
    total_cost BIGINT DEFAULT 0,

    -- Inventory
    warehouse_id UUID REFERENCES warehouses(id),
    batch_id UUID REFERENCES item_batches(id),

    -- Journal
    journal_id UUID REFERENCES journal_entries(id),

    completed_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_quality_status CHECK (quality_status IN ('passed', 'failed', 'rework'))
);

-- ============================================================================
-- 5. PRODUCTION SEQUENCES
-- ============================================================================

CREATE TABLE IF NOT EXISTS production_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'WO',
    last_reset_year INTEGER
);

-- ============================================================================
-- SEED ACCOUNTS (if not exist)
-- 1-10450 Barang Dalam Proses (Work In Progress - WIP)
-- 5-10150 Biaya Overhead Pabrik (Manufacturing Overhead)
-- 5-10160 Biaya Tenaga Kerja Langsung (Direct Labor)
-- ============================================================================

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE production_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_order_materials ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_order_labor ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_completions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_production_orders ON production_orders;
CREATE POLICY rls_production_orders ON production_orders
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_production_order_materials ON production_order_materials;
CREATE POLICY rls_production_order_materials ON production_order_materials
    USING (production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_production_order_labor ON production_order_labor;
CREATE POLICY rls_production_order_labor ON production_order_labor
    USING (production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_production_completions ON production_completions;
CREATE POLICY rls_production_completions ON production_completions
    USING (production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = current_setting('app.tenant_id', true)));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_production_orders_tenant ON production_orders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_production_orders_status ON production_orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_production_orders_product ON production_orders(product_id);
CREATE INDEX IF NOT EXISTS idx_production_orders_dates ON production_orders(planned_start_date, planned_end_date);
CREATE INDEX IF NOT EXISTS idx_production_order_materials_order ON production_order_materials(production_order_id);
CREATE INDEX IF NOT EXISTS idx_production_order_labor_order ON production_order_labor(production_order_id);
CREATE INDEX IF NOT EXISTS idx_production_completions_order ON production_completions(production_order_id);

-- ============================================================================
-- FUNCTION: Generate Production Order Number
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_production_order_number(p_tenant_id TEXT)
RETURNS TEXT AS $$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    INSERT INTO production_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'WO', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN production_sequences.last_reset_year != v_year THEN 1
            ELSE production_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- JOURNAL ENTRIES:
-- Issue Materials: Dr. WIP (1-10450) / Cr. Inventory (1-10400)
-- Record Labor: Dr. WIP (1-10450) / Cr. Direct Labor (5-10160) or Payroll Payable
-- Apply Overhead: Dr. WIP (1-10450) / Cr. Manufacturing Overhead (5-10150)
-- Complete Production: Dr. Finished Goods (1-10400) / Cr. WIP (1-10450)
-- ============================================================================
