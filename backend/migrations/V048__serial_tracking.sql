-- ============================================================================
-- V048: Serial Number Tracking (Nomor Seri)
-- ============================================================================
-- Purpose: Track individual units with unique serial numbers
-- Tables: item_serials, serial_movements
-- Extends: products, sales_invoice_items, sales_receipt_items, etc.
-- ============================================================================

-- ============================================================================
-- 1. EXTEND PRODUCTS TABLE
-- ============================================================================

ALTER TABLE products ADD COLUMN IF NOT EXISTS track_serial BOOLEAN DEFAULT false;

COMMENT ON COLUMN products.track_serial IS 'Enable serial number tracking for this item';

-- ============================================================================
-- 2. ITEM SERIALS TABLE - Serial number master
-- ============================================================================

CREATE TABLE IF NOT EXISTS item_serials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    item_id UUID NOT NULL,

    -- Serial identification
    serial_number VARCHAR(100) NOT NULL,

    -- Status: available -> reserved -> sold (or returned/damaged)
    status VARCHAR(20) DEFAULT 'available',

    -- Location
    warehouse_id UUID REFERENCES warehouses(id),

    -- Dates
    received_date DATE,
    sold_date DATE,
    warranty_start_date DATE,
    warranty_expiry DATE,

    -- Cost & Price
    unit_cost BIGINT DEFAULT 0,
    selling_price BIGINT DEFAULT 0,

    -- Source references (purchase)
    purchase_order_id UUID,
    bill_id UUID,
    supplier_serial VARCHAR(100), -- Original manufacturer serial

    -- Sale references
    sales_invoice_id UUID,
    sales_receipt_id UUID,
    customer_id UUID,

    -- Batch link (optional - serial within a batch)
    batch_id UUID REFERENCES item_batches(id),

    -- Condition
    condition VARCHAR(50) DEFAULT 'new', -- new, refurbished, used
    condition_notes TEXT,

    -- Notes
    notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_item_serials UNIQUE(tenant_id, item_id, serial_number),
    CONSTRAINT chk_is_status CHECK (status IN ('available', 'reserved', 'sold', 'returned', 'damaged', 'scrapped')),
    CONSTRAINT chk_is_condition CHECK (condition IN ('new', 'refurbished', 'used', 'damaged'))
);

COMMENT ON TABLE item_serials IS 'Serial number master for individual unit tracking';
COMMENT ON COLUMN item_serials.status IS 'available, reserved (for SO), sold, returned, damaged, scrapped';

-- ============================================================================
-- 3. SERIAL MOVEMENTS TABLE - Movement history
-- ============================================================================

CREATE TABLE IF NOT EXISTS serial_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    serial_id UUID NOT NULL REFERENCES item_serials(id) ON DELETE CASCADE,

    -- Movement info
    movement_type VARCHAR(50) NOT NULL,
    movement_date TIMESTAMPTZ DEFAULT NOW(),

    -- Location change
    from_warehouse_id UUID REFERENCES warehouses(id),
    to_warehouse_id UUID REFERENCES warehouses(id),

    -- Status change
    from_status VARCHAR(20),
    to_status VARCHAR(20),

    -- Reference document
    reference_type VARCHAR(50), -- purchase_order, bill, sales_invoice, sales_receipt, transfer, adjustment
    reference_id UUID,
    reference_number VARCHAR(50),

    -- Audit
    performed_by UUID,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_sm_type CHECK (movement_type IN (
        'received', 'transferred', 'reserved', 'sold', 'returned',
        'adjusted', 'damaged', 'scrapped', 'warranty_claim'
    ))
);

COMMENT ON TABLE serial_movements IS 'Movement history for serial numbers';
COMMENT ON COLUMN serial_movements.movement_type IS 'received, transferred, reserved, sold, returned, adjusted, damaged, scrapped, warranty_claim';

-- ============================================================================
-- 4. EXTEND TRANSACTION ITEMS TABLES (UUID arrays for multiple serials)
-- ============================================================================

-- Sales Invoice Items
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS serial_ids UUID[];

-- Sales Receipt Items
ALTER TABLE sales_receipt_items ADD COLUMN IF NOT EXISTS serial_ids UUID[];

-- Stock Adjustment Items
ALTER TABLE stock_adjustment_items ADD COLUMN IF NOT EXISTS serial_ids UUID[];

-- Stock Transfer Items
ALTER TABLE stock_transfer_items ADD COLUMN IF NOT EXISTS serial_ids UUID[];

-- ============================================================================
-- 5. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE item_serials ENABLE ROW LEVEL SECURITY;
ALTER TABLE serial_movements ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_item_serials ON item_serials;
DROP POLICY IF EXISTS rls_serial_movements ON serial_movements;

CREATE POLICY rls_item_serials ON item_serials
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_serial_movements ON serial_movements
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_is_tenant_item ON item_serials(tenant_id, item_id);
CREATE INDEX IF NOT EXISTS idx_is_serial_number ON item_serials(tenant_id, serial_number);
CREATE INDEX IF NOT EXISTS idx_is_status ON item_serials(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_is_warehouse ON item_serials(warehouse_id) WHERE status = 'available';
CREATE INDEX IF NOT EXISTS idx_is_batch ON item_serials(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_is_available ON item_serials(tenant_id, item_id, warehouse_id, status)
    WHERE status = 'available';
CREATE INDEX IF NOT EXISTS idx_is_warranty ON item_serials(tenant_id, warranty_expiry)
    WHERE warranty_expiry IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sm_serial ON serial_movements(serial_id);
CREATE INDEX IF NOT EXISTS idx_sm_type ON serial_movements(tenant_id, movement_type);
CREATE INDEX IF NOT EXISTS idx_sm_reference ON serial_movements(reference_type, reference_id);

-- GIN indexes for serial_ids arrays
CREATE INDEX IF NOT EXISTS idx_sii_serials ON sales_invoice_items USING GIN(serial_ids) WHERE serial_ids IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sri_serials ON sales_receipt_items USING GIN(serial_ids) WHERE serial_ids IS NOT NULL;

-- ============================================================================
-- 7. FUNCTIONS
-- ============================================================================

-- Search serial number globally
CREATE OR REPLACE FUNCTION search_serial_number(
    p_tenant_id TEXT,
    p_serial_number VARCHAR
) RETURNS TABLE(
    serial_id UUID,
    item_id UUID,
    item_name VARCHAR,
    serial_number VARCHAR,
    status VARCHAR,
    warehouse_id UUID,
    warehouse_name VARCHAR,
    sold_date DATE,
    customer_name VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.id as serial_id,
        s.item_id,
        i.nama_produk as item_name,
        s.serial_number,
        s.status,
        s.warehouse_id,
        w.name as warehouse_name,
        s.sold_date,
        c.nama as customer_name
    FROM item_serials s
    LEFT JOIN products i ON s.item_id = i.id
    LEFT JOIN warehouses w ON s.warehouse_id = w.id
    LEFT JOIN customers c ON s.customer_id::VARCHAR = c.id
    WHERE s.tenant_id = p_tenant_id
    AND s.serial_number ILIKE '%' || p_serial_number || '%'
    ORDER BY s.serial_number ASC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION search_serial_number IS 'Search serial number with partial match';

-- Get available serials for an item in a warehouse
CREATE OR REPLACE FUNCTION get_available_serials(
    p_tenant_id TEXT,
    p_item_id UUID,
    p_warehouse_id UUID,
    p_quantity INT DEFAULT NULL
) RETURNS TABLE(
    serial_id UUID,
    serial_number VARCHAR,
    unit_cost BIGINT,
    condition VARCHAR,
    received_date DATE
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.id as serial_id,
        s.serial_number,
        s.unit_cost,
        s.condition,
        s.received_date
    FROM item_serials s
    WHERE s.tenant_id = p_tenant_id
    AND s.item_id = p_item_id
    AND s.warehouse_id = p_warehouse_id
    AND s.status = 'available'
    ORDER BY s.received_date ASC, s.serial_number ASC
    LIMIT p_quantity;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_available_serials IS 'Returns available serial numbers for an item in warehouse';

-- Record serial movement
CREATE OR REPLACE FUNCTION record_serial_movement(
    p_tenant_id TEXT,
    p_serial_id UUID,
    p_movement_type VARCHAR,
    p_to_warehouse_id UUID DEFAULT NULL,
    p_to_status VARCHAR DEFAULT NULL,
    p_reference_type VARCHAR DEFAULT NULL,
    p_reference_id UUID DEFAULT NULL,
    p_reference_number VARCHAR DEFAULT NULL,
    p_performed_by UUID DEFAULT NULL,
    p_notes TEXT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    v_serial RECORD;
    v_movement_id UUID;
BEGIN
    -- Get current serial state
    SELECT * INTO v_serial
    FROM item_serials
    WHERE id = p_serial_id AND tenant_id = p_tenant_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Serial not found';
    END IF;

    -- Create movement record
    v_movement_id := gen_random_uuid();
    INSERT INTO serial_movements (
        id, tenant_id, serial_id, movement_type, movement_date,
        from_warehouse_id, to_warehouse_id,
        from_status, to_status,
        reference_type, reference_id, reference_number,
        performed_by, notes
    ) VALUES (
        v_movement_id, p_tenant_id, p_serial_id, p_movement_type, NOW(),
        v_serial.warehouse_id, COALESCE(p_to_warehouse_id, v_serial.warehouse_id),
        v_serial.status, COALESCE(p_to_status, v_serial.status),
        p_reference_type, p_reference_id, p_reference_number,
        p_performed_by, p_notes
    );

    -- Update serial
    UPDATE item_serials
    SET warehouse_id = COALESCE(p_to_warehouse_id, warehouse_id),
        status = COALESCE(p_to_status, status),
        updated_at = NOW()
    WHERE id = p_serial_id;

    RETURN v_movement_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION record_serial_movement IS 'Records a movement and updates serial status/location';

-- Mark serials as sold
CREATE OR REPLACE FUNCTION mark_serials_sold(
    p_tenant_id TEXT,
    p_serial_ids UUID[],
    p_sales_invoice_id UUID DEFAULT NULL,
    p_sales_receipt_id UUID DEFAULT NULL,
    p_customer_id UUID DEFAULT NULL,
    p_sold_by UUID DEFAULT NULL
) RETURNS INT AS $$
DECLARE
    v_serial_id UUID;
    v_count INT := 0;
    v_ref_type VARCHAR;
    v_ref_id UUID;
BEGIN
    IF p_sales_invoice_id IS NOT NULL THEN
        v_ref_type := 'sales_invoice';
        v_ref_id := p_sales_invoice_id;
    ELSE
        v_ref_type := 'sales_receipt';
        v_ref_id := p_sales_receipt_id;
    END IF;

    FOREACH v_serial_id IN ARRAY p_serial_ids
    LOOP
        -- Record movement
        PERFORM record_serial_movement(
            p_tenant_id, v_serial_id, 'sold',
            NULL, 'sold',
            v_ref_type, v_ref_id, NULL,
            p_sold_by, NULL
        );

        -- Update serial with sale info
        UPDATE item_serials
        SET sales_invoice_id = p_sales_invoice_id,
            sales_receipt_id = p_sales_receipt_id,
            customer_id = p_customer_id,
            sold_date = CURRENT_DATE,
            updated_at = NOW()
        WHERE id = v_serial_id AND tenant_id = p_tenant_id;

        v_count := v_count + 1;
    END LOOP;

    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mark_serials_sold IS 'Marks array of serial numbers as sold';

-- Get serial movement history
CREATE OR REPLACE FUNCTION get_serial_history(
    p_tenant_id TEXT,
    p_serial_id UUID
) RETURNS TABLE(
    movement_id UUID,
    movement_type VARCHAR,
    movement_date TIMESTAMPTZ,
    from_warehouse VARCHAR,
    to_warehouse VARCHAR,
    from_status VARCHAR,
    to_status VARCHAR,
    reference_type VARCHAR,
    reference_number VARCHAR,
    performed_by_name VARCHAR,
    notes TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        sm.id as movement_id,
        sm.movement_type,
        sm.movement_date,
        fw.name as from_warehouse,
        tw.name as to_warehouse,
        sm.from_status,
        sm.to_status,
        sm.reference_type,
        sm.reference_number,
        NULL::VARCHAR as performed_by_name, -- Join with users table if needed
        sm.notes
    FROM serial_movements sm
    LEFT JOIN warehouses fw ON sm.from_warehouse_id = fw.id
    LEFT JOIN warehouses tw ON sm.to_warehouse_id = tw.id
    WHERE sm.tenant_id = p_tenant_id
    AND sm.serial_id = p_serial_id
    ORDER BY sm.movement_date DESC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_serial_history IS 'Returns movement history for a serial number';

-- Count available serials per warehouse
CREATE OR REPLACE FUNCTION count_available_serials(
    p_tenant_id TEXT,
    p_item_id UUID
) RETURNS TABLE(
    warehouse_id UUID,
    warehouse_name VARCHAR,
    available_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.warehouse_id,
        w.name as warehouse_name,
        COUNT(*)::BIGINT as available_count
    FROM item_serials s
    JOIN warehouses w ON s.warehouse_id = w.id
    WHERE s.tenant_id = p_tenant_id
    AND s.item_id = p_item_id
    AND s.status = 'available'
    GROUP BY s.warehouse_id, w.name
    ORDER BY w.name;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION count_available_serials IS 'Returns count of available serials per warehouse';

-- ============================================================================
-- 8. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_item_serials_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_item_serials_updated_at ON item_serials;
CREATE TRIGGER trg_item_serials_updated_at
    BEFORE UPDATE ON item_serials
    FOR EACH ROW EXECUTE FUNCTION update_item_serials_updated_at();

-- Auto-create movement on status change
CREATE OR REPLACE FUNCTION auto_record_serial_movement()
RETURNS TRIGGER AS $$
BEGIN
    -- Only if status or warehouse changed
    IF OLD.status IS DISTINCT FROM NEW.status OR OLD.warehouse_id IS DISTINCT FROM NEW.warehouse_id THEN
        INSERT INTO serial_movements (
            tenant_id, serial_id, movement_type, movement_date,
            from_warehouse_id, to_warehouse_id,
            from_status, to_status
        ) VALUES (
            NEW.tenant_id, NEW.id,
            CASE
                WHEN NEW.status = 'sold' THEN 'sold'
                WHEN NEW.status = 'returned' THEN 'returned'
                WHEN NEW.status = 'damaged' THEN 'damaged'
                WHEN NEW.status = 'scrapped' THEN 'scrapped'
                WHEN OLD.warehouse_id IS DISTINCT FROM NEW.warehouse_id THEN 'transferred'
                ELSE 'adjusted'
            END,
            NOW(),
            OLD.warehouse_id, NEW.warehouse_id,
            OLD.status, NEW.status
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_serial_movement ON item_serials;
CREATE TRIGGER trg_auto_serial_movement
    AFTER UPDATE ON item_serials
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status OR OLD.warehouse_id IS DISTINCT FROM NEW.warehouse_id)
    EXECUTE FUNCTION auto_record_serial_movement();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V048: Serial Tracking created successfully';
    RAISE NOTICE 'Tables: item_serials, serial_movements';
    RAISE NOTICE 'Extended products with: track_serial';
    RAISE NOTICE 'Extended transaction items with serial_ids (UUID[])';
    RAISE NOTICE 'Functions: search_serial_number, get_available_serials, mark_serials_sold, get_serial_history';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
