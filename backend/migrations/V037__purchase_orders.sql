-- ============================================================================
-- V037: Purchase Orders Module (PO / Pesanan Pembelian)
-- ============================================================================
-- Purpose: Track purchase orders for goods/services from vendors
-- NOTE: PO does NOT create journal entries - only Bill creation does
-- ============================================================================

-- ============================================================================
-- 1. PURCHASE ORDERS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- PO identification
    po_number VARCHAR(50) NOT NULL,

    -- Vendor reference
    vendor_id UUID,
    vendor_name VARCHAR(255) NOT NULL,

    -- Amounts (calculated from items)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,

    -- Received/Billed tracking
    amount_received BIGINT DEFAULT 0,
    amount_billed BIGINT DEFAULT 0,

    -- Status: draft -> sent -> partial_received -> received -> partial_billed -> billed -> closed
    status VARCHAR(30) DEFAULT 'draft',

    -- Dates
    po_date DATE NOT NULL,
    expected_date DATE,
    ship_to_address TEXT,

    -- Reference
    ref_no VARCHAR(100),
    notes TEXT,

    -- Status tracking
    sent_at TIMESTAMPTZ,
    sent_by UUID,
    cancelled_at TIMESTAMPTZ,
    cancelled_by UUID,
    cancelled_reason TEXT,
    closed_at TIMESTAMPTZ,
    closed_by UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_po_tenant_number UNIQUE(tenant_id, po_number),
    CONSTRAINT chk_po_status CHECK (status IN (
        'draft', 'sent', 'partial_received', 'received',
        'partial_billed', 'billed', 'closed', 'cancelled'
    ))
);

COMMENT ON TABLE purchase_orders IS 'Pesanan Pembelian - Purchase Orders for procurement';
COMMENT ON COLUMN purchase_orders.status IS 'Workflow: draft->sent->partial_received/received->partial_billed/billed->closed';
COMMENT ON COLUMN purchase_orders.expected_date IS 'Tanggal perkiraan barang diterima';

-- ============================================================================
-- 2. PURCHASE ORDER ITEMS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id UUID NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,

    -- Product reference
    item_id UUID,
    item_code VARCHAR(50),
    description VARCHAR(500) NOT NULL,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL,
    quantity_received DECIMAL(15,4) DEFAULT 0,
    quantity_billed DECIMAL(15,4) DEFAULT 0,
    unit VARCHAR(20),
    unit_price BIGINT NOT NULL,

    -- Discounts
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,

    -- Tax
    tax_code VARCHAR(20),
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,

    -- Totals
    subtotal BIGINT NOT NULL,
    total BIGINT NOT NULL,

    -- Display order
    line_number INT DEFAULT 1,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE purchase_order_items IS 'Line items for purchase orders';
COMMENT ON COLUMN purchase_order_items.quantity_received IS 'Jumlah yang sudah diterima';
COMMENT ON COLUMN purchase_order_items.quantity_billed IS 'Jumlah yang sudah ditagih';

-- ============================================================================
-- 3. ADD purchase_order_id TO BILLS TABLE (if not exists)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bills' AND column_name = 'purchase_order_id'
    ) THEN
        ALTER TABLE bills ADD COLUMN purchase_order_id UUID REFERENCES purchase_orders(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_bills_purchase_order ON bills(purchase_order_id);

-- ============================================================================
-- 4. PURCHASE ORDER SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS purchase_order_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'PO',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_po_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_po_tenant_status ON purchase_orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_po_tenant_date ON purchase_orders(tenant_id, po_date);
CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_po_vendor_name ON purchase_orders(tenant_id, vendor_name);
CREATE INDEX IF NOT EXISTS idx_po_number ON purchase_orders(tenant_id, po_number);
CREATE INDEX IF NOT EXISTS idx_po_expected ON purchase_orders(tenant_id, expected_date);

CREATE INDEX IF NOT EXISTS idx_po_items_po ON purchase_order_items(po_id);
CREATE INDEX IF NOT EXISTS idx_po_items_item ON purchase_order_items(item_id);

-- ============================================================================
-- 6. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE purchase_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchase_order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchase_order_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_purchase_orders ON purchase_orders;
DROP POLICY IF EXISTS rls_purchase_order_items ON purchase_order_items;
DROP POLICY IF EXISTS rls_purchase_order_sequences ON purchase_order_sequences;

CREATE POLICY rls_purchase_orders ON purchase_orders
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_purchase_order_items ON purchase_order_items
    FOR ALL USING (po_id IN (
        SELECT id FROM purchase_orders WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_purchase_order_sequences ON purchase_order_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 7. FUNCTIONS
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_purchase_order_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'PO'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_po_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO purchase_order_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = purchase_order_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: PO-YYMM-0001
    v_po_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_po_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 8. TRIGGERS
-- ============================================================================

-- Auto-recalculate PO totals when items change
CREATE OR REPLACE FUNCTION update_purchase_order_totals()
RETURNS TRIGGER AS $$
DECLARE
    v_po_id UUID;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_po_id := OLD.po_id;
    ELSE
        v_po_id := NEW.po_id;
    END IF;

    SELECT
        COALESCE(SUM(subtotal), 0),
        COALESCE(SUM(tax_amount), 0),
        COALESCE(SUM(total), 0)
    INTO v_subtotal, v_tax_amount, v_total
    FROM purchase_order_items
    WHERE po_id = v_po_id;

    UPDATE purchase_orders
    SET subtotal = v_subtotal,
        tax_amount = v_tax_amount,
        total_amount = v_total,
        updated_at = NOW()
    WHERE id = v_po_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_po_totals ON purchase_order_items;
CREATE TRIGGER trg_update_po_totals
    AFTER INSERT OR UPDATE OR DELETE ON purchase_order_items
    FOR EACH ROW EXECUTE FUNCTION update_purchase_order_totals();

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_purchase_orders_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_purchase_orders_updated_at ON purchase_orders;
CREATE TRIGGER trg_purchase_orders_updated_at
    BEFORE UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION update_purchase_orders_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V037: Purchase Orders created successfully';
    RAISE NOTICE 'Tables: purchase_orders, purchase_order_items, purchase_order_sequences';
    RAISE NOTICE 'Added purchase_order_id to bills table';
    RAISE NOTICE 'NOTE: PO does NOT create journal entries - journal created on Bill';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
