-- ============================================================================
-- V043: Warehouses & Multi-Location Inventory
-- ============================================================================
-- Purpose: Multi-warehouse/branch support for inventory management
-- Tables: warehouses, warehouse_stock
-- Extends: sales_invoices, sales_orders, bills, purchase_orders,
--          stock_adjustments, inventory_ledger with warehouse_id
-- ============================================================================

-- ============================================================================
-- 1. WAREHOUSES TABLE - Master data for locations
-- ============================================================================

CREATE TABLE IF NOT EXISTS warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identification
    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,

    -- Address
    address TEXT,
    city VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100) DEFAULT 'Indonesia',

    -- Contact
    phone VARCHAR(50),
    email VARCHAR(100),
    manager_name VARCHAR(100),

    -- Settings
    is_default BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,

    -- For POS/Branch identification
    is_branch BOOLEAN DEFAULT false,
    branch_code VARCHAR(50),

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_warehouses_code UNIQUE(tenant_id, code)
);

COMMENT ON TABLE warehouses IS 'Warehouse/Location master for multi-location inventory';
COMMENT ON COLUMN warehouses.is_default IS 'Default warehouse for transactions without explicit warehouse';
COMMENT ON COLUMN warehouses.is_branch IS 'True if this is a retail branch/outlet';

-- ============================================================================
-- 2. WAREHOUSE_STOCK TABLE - Stock per warehouse per item
-- ============================================================================

CREATE TABLE IF NOT EXISTS warehouse_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    warehouse_id UUID NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    item_id UUID NOT NULL,

    -- Stock quantities
    quantity DECIMAL(15,4) DEFAULT 0,
    reserved_quantity DECIMAL(15,4) DEFAULT 0, -- Reserved for SO not yet shipped
    available_quantity DECIMAL(15,4) GENERATED ALWAYS AS (quantity - reserved_quantity) STORED,

    -- Reorder settings per warehouse
    reorder_level DECIMAL(15,4),
    reorder_quantity DECIMAL(15,4),
    min_stock DECIMAL(15,4),
    max_stock DECIMAL(15,4),

    -- Last activity
    last_stock_date TIMESTAMPTZ,
    last_count_date TIMESTAMPTZ,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_warehouse_stock UNIQUE(tenant_id, warehouse_id, item_id),
    CONSTRAINT chk_ws_quantity CHECK (quantity >= 0),
    CONSTRAINT chk_ws_reserved CHECK (reserved_quantity >= 0)
);

COMMENT ON TABLE warehouse_stock IS 'Current stock levels per warehouse per item';
COMMENT ON COLUMN warehouse_stock.reserved_quantity IS 'Quantity reserved by Sales Orders not yet shipped';
COMMENT ON COLUMN warehouse_stock.available_quantity IS 'Computed: quantity - reserved_quantity';

-- ============================================================================
-- 3. ADD WAREHOUSE_ID TO EXISTING TABLES
-- ============================================================================

-- Sales Invoices
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id);

-- Sales Orders (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'sales_orders') THEN
        EXECUTE 'ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id)';
    END IF;
END $$;

-- Bills
ALTER TABLE bills ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id);

-- Purchase Orders (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'purchase_orders') THEN
        EXECUTE 'ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id)';
    END IF;
END $$;

-- Stock Adjustments
ALTER TABLE stock_adjustments ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id);

-- Inventory Ledger
ALTER TABLE inventory_ledger ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id);

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE warehouses ENABLE ROW LEVEL SECURITY;
ALTER TABLE warehouse_stock ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_warehouses ON warehouses;
DROP POLICY IF EXISTS rls_warehouse_stock ON warehouse_stock;

CREATE POLICY rls_warehouses ON warehouses
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_warehouse_stock ON warehouse_stock
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_warehouses_tenant ON warehouses(tenant_id);
CREATE INDEX IF NOT EXISTS idx_warehouses_code ON warehouses(tenant_id, code);
CREATE INDEX IF NOT EXISTS idx_warehouses_default ON warehouses(tenant_id, is_default) WHERE is_default = true;
CREATE INDEX IF NOT EXISTS idx_warehouses_active ON warehouses(tenant_id, is_active) WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_ws_warehouse ON warehouse_stock(warehouse_id);
CREATE INDEX IF NOT EXISTS idx_ws_item ON warehouse_stock(item_id);
CREATE INDEX IF NOT EXISTS idx_ws_qty ON warehouse_stock(warehouse_id, quantity) WHERE quantity > 0;
CREATE INDEX IF NOT EXISTS idx_ws_reorder ON warehouse_stock(tenant_id, warehouse_id)
    WHERE quantity <= reorder_level AND reorder_level IS NOT NULL;

-- Add indexes on existing tables for warehouse_id
CREATE INDEX IF NOT EXISTS idx_si_warehouse ON sales_invoices(warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bills_warehouse ON bills(warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sa_warehouse ON stock_adjustments(warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_il_warehouse ON inventory_ledger(warehouse_id) WHERE warehouse_id IS NOT NULL;

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

-- Get default warehouse for tenant
CREATE OR REPLACE FUNCTION get_default_warehouse(p_tenant_id TEXT)
RETURNS UUID AS $$
DECLARE
    v_warehouse_id UUID;
BEGIN
    SELECT id INTO v_warehouse_id
    FROM warehouses
    WHERE tenant_id = p_tenant_id AND is_default = true AND is_active = true
    LIMIT 1;

    -- If no default, get first active warehouse
    IF v_warehouse_id IS NULL THEN
        SELECT id INTO v_warehouse_id
        FROM warehouses
        WHERE tenant_id = p_tenant_id AND is_active = true
        ORDER BY created_at ASC
        LIMIT 1;
    END IF;

    RETURN v_warehouse_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_default_warehouse IS 'Returns the default warehouse or first active warehouse for a tenant';

-- Get stock for item across all warehouses
CREATE OR REPLACE FUNCTION get_item_total_stock(p_tenant_id TEXT, p_item_id UUID)
RETURNS DECIMAL AS $$
DECLARE
    v_total DECIMAL;
BEGIN
    SELECT COALESCE(SUM(quantity), 0) INTO v_total
    FROM warehouse_stock
    WHERE tenant_id = p_tenant_id AND item_id = p_item_id;

    RETURN v_total;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_item_total_stock IS 'Returns total stock quantity for an item across all warehouses';

-- Get available stock for item in specific warehouse
CREATE OR REPLACE FUNCTION get_available_stock(
    p_tenant_id TEXT,
    p_warehouse_id UUID,
    p_item_id UUID
) RETURNS DECIMAL AS $$
DECLARE
    v_available DECIMAL;
BEGIN
    SELECT COALESCE(available_quantity, 0) INTO v_available
    FROM warehouse_stock
    WHERE tenant_id = p_tenant_id
    AND warehouse_id = p_warehouse_id
    AND item_id = p_item_id;

    RETURN COALESCE(v_available, 0);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_available_stock IS 'Returns available (non-reserved) stock for an item in a warehouse';

-- ============================================================================
-- 7. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_warehouses_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_warehouses_updated_at ON warehouses;
CREATE TRIGGER trg_warehouses_updated_at
    BEFORE UPDATE ON warehouses
    FOR EACH ROW EXECUTE FUNCTION update_warehouses_updated_at();

DROP TRIGGER IF EXISTS trg_warehouse_stock_updated_at ON warehouse_stock;
CREATE TRIGGER trg_warehouse_stock_updated_at
    BEFORE UPDATE ON warehouse_stock
    FOR EACH ROW EXECUTE FUNCTION update_warehouses_updated_at();

-- Ensure only one default warehouse per tenant
CREATE OR REPLACE FUNCTION ensure_single_default_warehouse()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_default = true THEN
        UPDATE warehouses
        SET is_default = false
        WHERE tenant_id = NEW.tenant_id AND id != NEW.id AND is_default = true;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_single_default_warehouse ON warehouses;
CREATE TRIGGER trg_single_default_warehouse
    BEFORE INSERT OR UPDATE ON warehouses
    FOR EACH ROW
    WHEN (NEW.is_default = true)
    EXECUTE FUNCTION ensure_single_default_warehouse();

-- Update warehouse_stock on inventory_ledger insert
CREATE OR REPLACE FUNCTION update_warehouse_stock_from_ledger()
RETURNS TRIGGER AS $$
BEGIN
    -- Only process if warehouse_id is set
    IF NEW.warehouse_id IS NOT NULL THEN
        INSERT INTO warehouse_stock (tenant_id, warehouse_id, item_id, quantity, last_stock_date)
        VALUES (NEW.tenant_id, NEW.warehouse_id, NEW.item_id, NEW.quantity_change, NOW())
        ON CONFLICT (tenant_id, warehouse_id, item_id)
        DO UPDATE SET
            quantity = warehouse_stock.quantity + EXCLUDED.quantity,
            last_stock_date = NOW(),
            updated_at = NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_warehouse_stock ON inventory_ledger;
CREATE TRIGGER trg_update_warehouse_stock
    AFTER INSERT ON inventory_ledger
    FOR EACH ROW
    EXECUTE FUNCTION update_warehouse_stock_from_ledger();

-- ============================================================================
-- 8. SEED DATA FUNCTION
-- ============================================================================

-- Create default warehouse for existing tenants
CREATE OR REPLACE FUNCTION seed_default_warehouses()
RETURNS void AS $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT DISTINCT tenant_id
        FROM chart_of_accounts
        WHERE tenant_id NOT IN (SELECT DISTINCT tenant_id FROM warehouses)
    LOOP
        INSERT INTO warehouses (tenant_id, code, name, is_default, is_active)
        VALUES (r.tenant_id, 'WH-MAIN', 'Gudang Utama', true, true)
        ON CONFLICT (tenant_id, code) DO NOTHING;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Execute seed
SELECT seed_default_warehouses();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V043: Warehouses & Multi-Location created successfully';
    RAISE NOTICE 'Tables: warehouses, warehouse_stock';
    RAISE NOTICE 'Added warehouse_id to: sales_invoices, bills, stock_adjustments, inventory_ledger';
    RAISE NOTICE 'RLS enabled on all tables';
    RAISE NOTICE 'Default warehouse (WH-MAIN) seeded for existing tenants';
END $$;
