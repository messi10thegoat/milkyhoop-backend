-- ============================================================================
-- V040: Sales Orders Module
-- ============================================================================
-- Purpose: Sales order management with shipment tracking
-- NO journal entries - accounting impact happens on Invoice creation
-- ============================================================================

-- ============================================================================
-- 1. SALES ORDERS TABLE - Order header
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Header
    order_number VARCHAR(50) NOT NULL,
    order_date DATE NOT NULL,
    expected_ship_date DATE,

    -- Customer
    customer_id UUID NOT NULL,
    customer_name VARCHAR(255) NOT NULL,

    -- Reference
    quote_id UUID REFERENCES quotes(id),
    reference VARCHAR(100),

    -- Shipping
    shipping_address TEXT,
    shipping_method VARCHAR(100),

    -- Amounts (stored as BIGINT - smallest currency unit)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    shipping_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL DEFAULT 0,

    -- Status tracking
    status VARCHAR(30) DEFAULT 'draft', -- draft, confirmed, partial_shipped, shipped, partial_invoiced, invoiced, completed, cancelled

    -- Fulfillment tracking (denormalized)
    shipped_qty DECIMAL(15,4) DEFAULT 0,
    invoiced_qty DECIMAL(15,4) DEFAULT 0,

    -- Notes
    notes TEXT,
    internal_notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    confirmed_at TIMESTAMPTZ,
    confirmed_by UUID,

    CONSTRAINT uq_sales_orders_number UNIQUE(tenant_id, order_number),
    CONSTRAINT chk_sales_order_status CHECK (status IN (
        'draft', 'confirmed', 'partial_shipped', 'shipped',
        'partial_invoiced', 'invoiced', 'completed', 'cancelled'
    ))
);

COMMENT ON TABLE sales_orders IS 'Sales Orders - Order confirmation before delivery and invoicing';
COMMENT ON COLUMN sales_orders.status IS 'Workflow: draft -> confirmed -> shipped -> invoiced -> completed';

-- ============================================================================
-- 2. SALES ORDER ITEMS TABLE - Line items with quantity tracking
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,

    -- Item reference
    item_id UUID,
    description TEXT NOT NULL,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL DEFAULT 1,
    quantity_shipped DECIMAL(15,4) DEFAULT 0,
    quantity_invoiced DECIMAL(15,4) DEFAULT 0,
    unit VARCHAR(50),

    -- Pricing (stored as BIGINT)
    unit_price BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    tax_id UUID,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    line_total BIGINT NOT NULL DEFAULT 0,

    -- Inventory tracking (optional)
    warehouse_id UUID,

    -- Sort order
    sort_order INTEGER DEFAULT 0
);

COMMENT ON TABLE sales_order_items IS 'Sales order line items with shipment/invoice quantity tracking';

-- ============================================================================
-- 3. SHIPMENTS TABLE - Delivery orders
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_order_shipments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),

    -- Shipment info
    shipment_number VARCHAR(50) NOT NULL,
    shipment_date DATE NOT NULL,

    -- Carrier
    carrier VARCHAR(100),
    tracking_number VARCHAR(100),

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, shipped, delivered

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    shipped_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,

    CONSTRAINT uq_shipments_number UNIQUE(tenant_id, shipment_number),
    CONSTRAINT chk_shipment_status CHECK (status IN ('pending', 'shipped', 'delivered'))
);

COMMENT ON TABLE sales_order_shipments IS 'Shipment/Delivery orders for sales orders';

-- ============================================================================
-- 4. SHIPMENT ITEMS TABLE - Items in each shipment
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_order_shipment_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id UUID NOT NULL REFERENCES sales_order_shipments(id) ON DELETE CASCADE,
    sales_order_item_id UUID NOT NULL REFERENCES sales_order_items(id),

    quantity_shipped DECIMAL(15,4) NOT NULL
);

-- ============================================================================
-- 5. SEQUENCE TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_order_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'SO',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_so_seq_tenant_month UNIQUE(tenant_id, year_month)
);

CREATE TABLE IF NOT EXISTS shipment_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'SHP',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_shp_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 6. ADD sales_order_id TO sales_invoices
-- ============================================================================

ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS sales_order_id UUID REFERENCES sales_orders(id);
CREATE INDEX IF NOT EXISTS idx_invoices_sales_order ON sales_invoices(sales_order_id) WHERE sales_order_id IS NOT NULL;

-- Also add quote_id if not exists
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS quote_id UUID REFERENCES quotes(id);
CREATE INDEX IF NOT EXISTS idx_invoices_quote ON sales_invoices(quote_id) WHERE quote_id IS NOT NULL;

-- ============================================================================
-- 7. INDEXES
-- ============================================================================

-- Sales orders
CREATE INDEX IF NOT EXISTS idx_sales_orders_tenant ON sales_orders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sales_orders_customer ON sales_orders(customer_id, status);
CREATE INDEX IF NOT EXISTS idx_sales_orders_status ON sales_orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_sales_orders_number ON sales_orders(tenant_id, order_number);
CREATE INDEX IF NOT EXISTS idx_sales_orders_date ON sales_orders(tenant_id, order_date DESC);
CREATE INDEX IF NOT EXISTS idx_sales_orders_quote ON sales_orders(quote_id) WHERE quote_id IS NOT NULL;

-- Sales order items
CREATE INDEX IF NOT EXISTS idx_so_items_order ON sales_order_items(sales_order_id);
CREATE INDEX IF NOT EXISTS idx_so_items_item ON sales_order_items(item_id) WHERE item_id IS NOT NULL;

-- Shipments
CREATE INDEX IF NOT EXISTS idx_shipments_tenant ON sales_order_shipments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_shipments_order ON sales_order_shipments(sales_order_id);
CREATE INDEX IF NOT EXISTS idx_shipments_date ON sales_order_shipments(tenant_id, shipment_date DESC);

-- Shipment items
CREATE INDEX IF NOT EXISTS idx_shipment_items_shipment ON sales_order_shipment_items(shipment_id);
CREATE INDEX IF NOT EXISTS idx_shipment_items_so_item ON sales_order_shipment_items(sales_order_item_id);

-- ============================================================================
-- 8. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE sales_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_order_shipments ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_order_shipment_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_order_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE shipment_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_sales_orders ON sales_orders;
DROP POLICY IF EXISTS rls_sales_order_items ON sales_order_items;
DROP POLICY IF EXISTS rls_sales_order_shipments ON sales_order_shipments;
DROP POLICY IF EXISTS rls_sales_order_shipment_items ON sales_order_shipment_items;
DROP POLICY IF EXISTS rls_sales_order_sequences ON sales_order_sequences;
DROP POLICY IF EXISTS rls_shipment_sequences ON shipment_sequences;

CREATE POLICY rls_sales_orders ON sales_orders
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_sales_order_items ON sales_order_items
    FOR ALL USING (sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = current_setting('app.tenant_id', true)));

CREATE POLICY rls_sales_order_shipments ON sales_order_shipments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_sales_order_shipment_items ON sales_order_shipment_items
    FOR ALL USING (shipment_id IN (SELECT id FROM sales_order_shipments WHERE tenant_id = current_setting('app.tenant_id', true)));

CREATE POLICY rls_sales_order_sequences ON sales_order_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_shipment_sequences ON shipment_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 9. FUNCTIONS
-- ============================================================================

-- Generate sales order number
CREATE OR REPLACE FUNCTION generate_sales_order_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'SO'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_order_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO sales_order_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = sales_order_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: SO-YYMM-0001
    v_order_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_order_number;
END;
$$ LANGUAGE plpgsql;

-- Generate shipment number
CREATE OR REPLACE FUNCTION generate_shipment_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'SHP'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_shipment_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO shipment_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = shipment_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: SHP-YYMM-0001
    v_shipment_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_shipment_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 10. TRIGGERS
-- ============================================================================

-- Update SO status based on item quantities
CREATE OR REPLACE FUNCTION update_sales_order_status()
RETURNS TRIGGER AS $$
DECLARE
    v_so_id UUID;
    v_total_qty DECIMAL;
    v_shipped_qty DECIMAL;
    v_invoiced_qty DECIMAL;
    v_current_status VARCHAR(30);
    v_new_status VARCHAR(30);
BEGIN
    -- Get the sales order ID
    v_so_id := COALESCE(NEW.sales_order_id, OLD.sales_order_id);

    -- Get totals
    SELECT
        COALESCE(SUM(quantity), 0),
        COALESCE(SUM(quantity_shipped), 0),
        COALESCE(SUM(quantity_invoiced), 0)
    INTO v_total_qty, v_shipped_qty, v_invoiced_qty
    FROM sales_order_items
    WHERE sales_order_id = v_so_id;

    -- Get current status
    SELECT status INTO v_current_status FROM sales_orders WHERE id = v_so_id;

    -- Don't update cancelled orders
    IF v_current_status = 'cancelled' THEN
        RETURN NEW;
    END IF;

    -- Determine new status
    IF v_current_status = 'draft' THEN
        v_new_status := 'draft';
    ELSIF v_invoiced_qty >= v_total_qty THEN
        v_new_status := 'invoiced';
    ELSIF v_invoiced_qty > 0 THEN
        v_new_status := 'partial_invoiced';
    ELSIF v_shipped_qty >= v_total_qty THEN
        v_new_status := 'shipped';
    ELSIF v_shipped_qty > 0 THEN
        v_new_status := 'partial_shipped';
    ELSE
        v_new_status := 'confirmed';
    END IF;

    -- Update order
    UPDATE sales_orders
    SET
        status = v_new_status,
        shipped_qty = v_shipped_qty,
        invoiced_qty = v_invoiced_qty,
        updated_at = NOW()
    WHERE id = v_so_id
    AND (status != v_new_status OR shipped_qty != v_shipped_qty OR invoiced_qty != v_invoiced_qty);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_so_status ON sales_order_items;
CREATE TRIGGER trg_update_so_status
AFTER UPDATE ON sales_order_items
FOR EACH ROW
EXECUTE FUNCTION update_sales_order_status();

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_sales_orders_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sales_orders_updated_at ON sales_orders;
CREATE TRIGGER trg_sales_orders_updated_at
BEFORE UPDATE ON sales_orders
FOR EACH ROW EXECUTE FUNCTION update_sales_orders_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V040: Sales Orders Module created successfully';
    RAISE NOTICE 'Tables: sales_orders, sales_order_items, sales_order_shipments, sales_order_shipment_items';
    RAISE NOTICE 'Sequences: sales_order_sequences, shipment_sequences';
    RAISE NOTICE 'Added sales_order_id and quote_id columns to sales_invoices';
    RAISE NOTICE 'RLS enabled on all tables';
    RAISE NOTICE 'NOTE: NO journal entries - sales orders are pre-accounting documents';
END $$;
