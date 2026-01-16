-- ============================================================================
-- V047: Batch Tracking (Nomor Lot & Kedaluwarsa)
-- ============================================================================
-- Purpose: Track inventory by batch/lot number with expiry dates
-- Tables: item_batches, batch_warehouse_stock
-- Extends: products, sales_invoice_items, sales_receipt_items, etc.
-- Default selection method: FEFO (First Expiry First Out)
-- ============================================================================

-- ============================================================================
-- 1. EXTEND PRODUCTS TABLE
-- ============================================================================

ALTER TABLE products ADD COLUMN IF NOT EXISTS track_batches BOOLEAN DEFAULT false;
ALTER TABLE products ADD COLUMN IF NOT EXISTS track_expiry BOOLEAN DEFAULT false;
ALTER TABLE products ADD COLUMN IF NOT EXISTS default_expiry_days INTEGER; -- Auto-calculate expiry from manufacture

COMMENT ON COLUMN products.track_batches IS 'Enable batch/lot tracking for this item';
COMMENT ON COLUMN products.track_expiry IS 'Enable expiry date tracking for this item';
COMMENT ON COLUMN products.default_expiry_days IS 'Default shelf life in days from manufacture date';

-- ============================================================================
-- 2. ITEM BATCHES TABLE - Batch master
-- ============================================================================

CREATE TABLE IF NOT EXISTS item_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    item_id UUID NOT NULL,

    -- Batch identification
    batch_number VARCHAR(100) NOT NULL,

    -- Dates
    manufacture_date DATE,
    expiry_date DATE,
    received_date DATE,

    -- Quantity tracking (total across all warehouses)
    initial_quantity DECIMAL(15,4) NOT NULL,
    current_quantity DECIMAL(15,4) NOT NULL,

    -- Cost
    unit_cost BIGINT DEFAULT 0,
    total_value BIGINT DEFAULT 0,

    -- Status
    status VARCHAR(20) DEFAULT 'active', -- active, expired, depleted, quarantine

    -- Source reference
    purchase_order_id UUID,
    bill_id UUID,
    supplier_batch_number VARCHAR(100),

    -- Quality
    quality_grade VARCHAR(50),
    quality_notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_item_batches UNIQUE(tenant_id, item_id, batch_number),
    CONSTRAINT chk_ib_status CHECK (status IN ('active', 'expired', 'depleted', 'quarantine')),
    CONSTRAINT chk_ib_qty CHECK (current_quantity >= 0)
);

COMMENT ON TABLE item_batches IS 'Batch/Lot master for inventory tracking';
COMMENT ON COLUMN item_batches.status IS 'active, expired, depleted (qty=0), quarantine (hold)';

-- ============================================================================
-- 3. BATCH WAREHOUSE STOCK TABLE - Batch quantity per warehouse
-- ============================================================================

CREATE TABLE IF NOT EXISTS batch_warehouse_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    batch_id UUID NOT NULL REFERENCES item_batches(id) ON DELETE CASCADE,
    warehouse_id UUID NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,

    -- Quantities
    quantity DECIMAL(15,4) DEFAULT 0,
    reserved_quantity DECIMAL(15,4) DEFAULT 0,
    available_quantity DECIMAL(15,4) GENERATED ALWAYS AS (quantity - reserved_quantity) STORED,

    -- Audit
    last_movement_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_batch_warehouse UNIQUE(batch_id, warehouse_id),
    CONSTRAINT chk_bws_qty CHECK (quantity >= 0),
    CONSTRAINT chk_bws_reserved CHECK (reserved_quantity >= 0)
);

COMMENT ON TABLE batch_warehouse_stock IS 'Batch quantity per warehouse location';

-- ============================================================================
-- 4. EXTEND TRANSACTION ITEMS TABLES
-- ============================================================================

-- Sales Invoice Items
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES item_batches(id);

-- Sales Receipt Items
ALTER TABLE sales_receipt_items ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES item_batches(id);

-- Stock Adjustment Items
ALTER TABLE stock_adjustment_items ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES item_batches(id);

-- Stock Transfer Items (already has batch_number, add batch_id)
ALTER TABLE stock_transfer_items ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES item_batches(id);

-- Inventory Ledger
ALTER TABLE inventory_ledger ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES item_batches(id);

-- Bill Items (extend existing batch_no with batch_id)
ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES item_batches(id);

-- Purchase Order Items
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'purchase_order_items') THEN
        EXECUTE 'ALTER TABLE purchase_order_items ADD COLUMN IF NOT EXISTS batch_number VARCHAR(100)';
        EXECUTE 'ALTER TABLE purchase_order_items ADD COLUMN IF NOT EXISTS expiry_date DATE';
    END IF;
END $$;

-- ============================================================================
-- 5. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE item_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE batch_warehouse_stock ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_item_batches ON item_batches;
DROP POLICY IF EXISTS rls_batch_warehouse_stock ON batch_warehouse_stock;

CREATE POLICY rls_item_batches ON item_batches
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_batch_warehouse_stock ON batch_warehouse_stock
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_ib_tenant_item ON item_batches(tenant_id, item_id);
CREATE INDEX IF NOT EXISTS idx_ib_batch_number ON item_batches(tenant_id, batch_number);
CREATE INDEX IF NOT EXISTS idx_ib_expiry ON item_batches(expiry_date) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_ib_status ON item_batches(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ib_expiring ON item_batches(tenant_id, expiry_date)
    WHERE status = 'active' AND expiry_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bws_batch ON batch_warehouse_stock(batch_id);
CREATE INDEX IF NOT EXISTS idx_bws_warehouse ON batch_warehouse_stock(warehouse_id);
CREATE INDEX IF NOT EXISTS idx_bws_available ON batch_warehouse_stock(batch_id, warehouse_id)
    WHERE quantity > 0;

-- Indexes on extended tables
CREATE INDEX IF NOT EXISTS idx_sii_batch ON sales_invoice_items(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sri_batch ON sales_receipt_items(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_il_batch ON inventory_ledger(batch_id) WHERE batch_id IS NOT NULL;

-- ============================================================================
-- 7. FUNCTIONS
-- ============================================================================

-- Get available batches for selection (FEFO default - First Expiry First Out)
CREATE OR REPLACE FUNCTION get_available_batches(
    p_tenant_id TEXT,
    p_item_id UUID,
    p_warehouse_id UUID,
    p_quantity_needed DECIMAL,
    p_method VARCHAR DEFAULT 'FEFO' -- FEFO or FIFO
) RETURNS TABLE(
    batch_id UUID,
    batch_number VARCHAR,
    expiry_date DATE,
    available_quantity DECIMAL,
    quantity_to_use DECIMAL,
    unit_cost BIGINT
) AS $$
DECLARE
    v_remaining DECIMAL := p_quantity_needed;
    v_order_by TEXT;
    r RECORD;
BEGIN
    -- Determine sort order
    IF p_method = 'FIFO' THEN
        v_order_by := 'ib.created_at ASC, ib.expiry_date ASC NULLS LAST';
    ELSE -- FEFO (default)
        v_order_by := 'ib.expiry_date ASC NULLS LAST, ib.created_at ASC';
    END IF;

    -- Loop through available batches
    FOR r IN EXECUTE format('
        SELECT
            ib.id as batch_id,
            ib.batch_number,
            ib.expiry_date,
            bws.available_quantity,
            ib.unit_cost
        FROM item_batches ib
        JOIN batch_warehouse_stock bws ON ib.id = bws.batch_id
        WHERE ib.tenant_id = $1
        AND ib.item_id = $2
        AND bws.warehouse_id = $3
        AND ib.status = ''active''
        AND bws.available_quantity > 0
        ORDER BY %s
    ', v_order_by)
    USING p_tenant_id, p_item_id, p_warehouse_id
    LOOP
        IF v_remaining <= 0 THEN
            EXIT;
        END IF;

        batch_id := r.batch_id;
        batch_number := r.batch_number;
        expiry_date := r.expiry_date;
        available_quantity := r.available_quantity;
        unit_cost := r.unit_cost;

        IF r.available_quantity >= v_remaining THEN
            quantity_to_use := v_remaining;
            v_remaining := 0;
        ELSE
            quantity_to_use := r.available_quantity;
            v_remaining := v_remaining - r.available_quantity;
        END IF;

        RETURN NEXT;
    END LOOP;

    RETURN;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_available_batches IS 'Returns batches to use based on FEFO (default) or FIFO method';

-- Get expiring batches
CREATE OR REPLACE FUNCTION get_expiring_batches(
    p_tenant_id TEXT,
    p_days_ahead INTEGER DEFAULT 30,
    p_warehouse_id UUID DEFAULT NULL
) RETURNS TABLE(
    batch_id UUID,
    item_id UUID,
    item_name VARCHAR,
    batch_number VARCHAR,
    expiry_date DATE,
    days_until_expiry INTEGER,
    quantity DECIMAL,
    warehouse_id UUID,
    warehouse_name VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ib.id as batch_id,
        ib.item_id,
        i.name as item_name,
        ib.batch_number,
        ib.expiry_date,
        (ib.expiry_date - CURRENT_DATE)::INTEGER as days_until_expiry,
        bws.quantity,
        bws.warehouse_id,
        w.name as warehouse_name
    FROM item_batches ib
    JOIN batch_warehouse_stock bws ON ib.id = bws.batch_id
    JOIN warehouses w ON bws.warehouse_id = w.id
    LEFT JOIN products i ON ib.item_id = i.id
    WHERE ib.tenant_id = p_tenant_id
    AND ib.status = 'active'
    AND ib.expiry_date IS NOT NULL
    AND ib.expiry_date <= (CURRENT_DATE + (p_days_ahead || ' days')::INTERVAL)
    AND bws.quantity > 0
    AND (p_warehouse_id IS NULL OR bws.warehouse_id = p_warehouse_id)
    ORDER BY ib.expiry_date ASC, bws.quantity DESC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_expiring_batches IS 'Returns batches expiring within specified days';

-- Get expired batches
CREATE OR REPLACE FUNCTION get_expired_batches(
    p_tenant_id TEXT,
    p_warehouse_id UUID DEFAULT NULL
) RETURNS TABLE(
    batch_id UUID,
    item_id UUID,
    item_name VARCHAR,
    batch_number VARCHAR,
    expiry_date DATE,
    days_expired INTEGER,
    quantity DECIMAL,
    total_value BIGINT,
    warehouse_id UUID,
    warehouse_name VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ib.id as batch_id,
        ib.item_id,
        i.name as item_name,
        ib.batch_number,
        ib.expiry_date,
        (CURRENT_DATE - ib.expiry_date)::INTEGER as days_expired,
        bws.quantity,
        (bws.quantity * ib.unit_cost)::BIGINT as total_value,
        bws.warehouse_id,
        w.name as warehouse_name
    FROM item_batches ib
    JOIN batch_warehouse_stock bws ON ib.id = bws.batch_id
    JOIN warehouses w ON bws.warehouse_id = w.id
    LEFT JOIN products i ON ib.item_id = i.id
    WHERE ib.tenant_id = p_tenant_id
    AND ib.expiry_date IS NOT NULL
    AND ib.expiry_date < CURRENT_DATE
    AND bws.quantity > 0
    AND (p_warehouse_id IS NULL OR bws.warehouse_id = p_warehouse_id)
    ORDER BY ib.expiry_date ASC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_expired_batches IS 'Returns batches that have expired with remaining quantity';

-- ============================================================================
-- 8. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_item_batches_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_item_batches_updated_at ON item_batches;
CREATE TRIGGER trg_item_batches_updated_at
    BEFORE UPDATE ON item_batches
    FOR EACH ROW EXECUTE FUNCTION update_item_batches_updated_at();

DROP TRIGGER IF EXISTS trg_bws_updated_at ON batch_warehouse_stock;
CREATE TRIGGER trg_bws_updated_at
    BEFORE UPDATE ON batch_warehouse_stock
    FOR EACH ROW EXECUTE FUNCTION update_item_batches_updated_at();

-- Auto-expire batches when expiry_date passed
CREATE OR REPLACE FUNCTION auto_expire_batches()
RETURNS TRIGGER AS $$
BEGIN
    -- Check if expiry date has passed
    IF NEW.expiry_date IS NOT NULL AND NEW.expiry_date < CURRENT_DATE AND OLD.status = 'active' THEN
        NEW.status := 'expired';
    END IF;

    -- Check if depleted
    IF NEW.current_quantity <= 0 AND OLD.status = 'active' THEN
        NEW.status := 'depleted';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_expire_batches ON item_batches;
CREATE TRIGGER trg_auto_expire_batches
    BEFORE UPDATE ON item_batches
    FOR EACH ROW EXECUTE FUNCTION auto_expire_batches();

-- Sync batch current_quantity with warehouse stock
CREATE OR REPLACE FUNCTION sync_batch_quantity()
RETURNS TRIGGER AS $$
DECLARE
    v_total DECIMAL;
    v_batch RECORD;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT ib.id, ib.tenant_id INTO v_batch FROM item_batches ib WHERE ib.id = OLD.batch_id;
    ELSE
        SELECT ib.id, ib.tenant_id INTO v_batch FROM item_batches ib WHERE ib.id = NEW.batch_id;
    END IF;

    IF v_batch.id IS NOT NULL THEN
        SELECT COALESCE(SUM(quantity), 0) INTO v_total
        FROM batch_warehouse_stock
        WHERE batch_id = v_batch.id;

        UPDATE item_batches
        SET current_quantity = v_total,
            updated_at = NOW()
        WHERE id = v_batch.id;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_batch_quantity ON batch_warehouse_stock;
CREATE TRIGGER trg_sync_batch_quantity
    AFTER INSERT OR UPDATE OR DELETE ON batch_warehouse_stock
    FOR EACH ROW EXECUTE FUNCTION sync_batch_quantity();

-- Update batch_warehouse_stock from inventory_ledger (if batch tracking)
CREATE OR REPLACE FUNCTION update_batch_stock_from_ledger()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.batch_id IS NOT NULL AND NEW.warehouse_id IS NOT NULL THEN
        INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity, last_movement_date)
        VALUES (NEW.tenant_id, NEW.batch_id, NEW.warehouse_id, NEW.quantity_change, NOW())
        ON CONFLICT (batch_id, warehouse_id)
        DO UPDATE SET
            quantity = batch_warehouse_stock.quantity + EXCLUDED.quantity,
            last_movement_date = NOW(),
            updated_at = NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_batch_stock ON inventory_ledger;
CREATE TRIGGER trg_update_batch_stock
    AFTER INSERT ON inventory_ledger
    FOR EACH ROW
    WHEN (NEW.batch_id IS NOT NULL)
    EXECUTE FUNCTION update_batch_stock_from_ledger();

-- ============================================================================
-- 9. SCHEDULED JOB FUNCTION (for daily expiry check)
-- ============================================================================

-- Function to mark expired batches (call via cron/scheduler)
CREATE OR REPLACE FUNCTION process_expired_batches()
RETURNS TABLE(
    tenant_id TEXT,
    batches_expired INT
) AS $$
DECLARE
    r RECORD;
    v_count INT;
BEGIN
    FOR r IN SELECT DISTINCT ib.tenant_id FROM item_batches ib
    LOOP
        UPDATE item_batches
        SET status = 'expired', updated_at = NOW()
        WHERE item_batches.tenant_id = r.tenant_id
        AND status = 'active'
        AND expiry_date IS NOT NULL
        AND expiry_date < CURRENT_DATE;

        GET DIAGNOSTICS v_count = ROW_COUNT;

        tenant_id := r.tenant_id;
        batches_expired := v_count;
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION process_expired_batches IS 'Marks expired batches - call daily via cron';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V047: Batch Tracking created successfully';
    RAISE NOTICE 'Tables: item_batches, batch_warehouse_stock';
    RAISE NOTICE 'Extended products with: track_batches, track_expiry, default_expiry_days';
    RAISE NOTICE 'Extended transaction items with batch_id';
    RAISE NOTICE 'Functions: get_available_batches (FEFO default), get_expiring_batches, get_expired_batches';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
