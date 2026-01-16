-- ============================================================================
-- V044: Stock Transfers (Transfer Stok Antar Gudang)
-- ============================================================================
-- Purpose: Transfer inventory between warehouses without accounting impact
-- Tables: stock_transfers, stock_transfer_items, stock_transfer_sequences
-- IMPORTANT: NO JOURNAL ENTRY - internal stock movement only
-- ============================================================================

-- ============================================================================
-- 1. STOCK TRANSFERS TABLE - Header
-- ============================================================================

CREATE TABLE IF NOT EXISTS stock_transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identification
    transfer_number VARCHAR(50) NOT NULL,
    transfer_date DATE NOT NULL,

    -- Locations
    from_warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    to_warehouse_id UUID NOT NULL REFERENCES warehouses(id),

    -- Status: draft -> in_transit -> received (or cancelled)
    status VARCHAR(20) DEFAULT 'draft',

    -- Dates
    shipped_date DATE,
    received_date DATE,
    expected_date DATE,

    -- Reference
    reference VARCHAR(100),
    notes TEXT,

    -- Totals (calculated)
    total_items INT DEFAULT 0,
    total_quantity DECIMAL(15,4) DEFAULT 0,
    total_value BIGINT DEFAULT 0,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    shipped_by UUID,
    received_by UUID,
    cancelled_by UUID,
    cancelled_at TIMESTAMPTZ,
    cancel_reason TEXT,

    CONSTRAINT uq_stock_transfers_number UNIQUE(tenant_id, transfer_number),
    CONSTRAINT chk_different_warehouses CHECK (from_warehouse_id != to_warehouse_id),
    CONSTRAINT chk_st_status CHECK (status IN ('draft', 'in_transit', 'received', 'cancelled'))
);

COMMENT ON TABLE stock_transfers IS 'Stock transfer header - NO ACCOUNTING IMPACT';
COMMENT ON COLUMN stock_transfers.status IS 'draft=editable, in_transit=shipped, received=completed, cancelled=voided';

-- ============================================================================
-- 2. STOCK TRANSFER ITEMS TABLE - Line items
-- ============================================================================

CREATE TABLE IF NOT EXISTS stock_transfer_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_transfer_id UUID NOT NULL REFERENCES stock_transfers(id) ON DELETE CASCADE,

    -- Product
    item_id UUID NOT NULL,
    item_code VARCHAR(50),
    item_name VARCHAR(255) NOT NULL,

    -- Quantities
    quantity_requested DECIMAL(15,4) NOT NULL,
    quantity_shipped DECIMAL(15,4) DEFAULT 0,
    quantity_received DECIMAL(15,4) DEFAULT 0,
    quantity_variance DECIMAL(15,4) GENERATED ALWAYS AS (quantity_shipped - quantity_received) STORED,
    unit VARCHAR(50),

    -- Cost tracking (for reporting, NOT for journal)
    unit_cost BIGINT DEFAULT 0,
    total_value BIGINT DEFAULT 0,

    -- Batch tracking (optional)
    batch_number VARCHAR(100),
    expiry_date DATE,

    -- Serial tracking (optional)
    serial_numbers TEXT[], -- Array of serial numbers

    line_number INT DEFAULT 1,
    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_sti_qty_requested CHECK (quantity_requested > 0),
    CONSTRAINT chk_sti_qty_shipped CHECK (quantity_shipped >= 0),
    CONSTRAINT chk_sti_qty_received CHECK (quantity_received >= 0)
);

COMMENT ON TABLE stock_transfer_items IS 'Line items for stock transfers';
COMMENT ON COLUMN stock_transfer_items.quantity_variance IS 'Difference: shipped - received (discrepancy)';

-- ============================================================================
-- 3. SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS stock_transfer_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'ST',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_st_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE stock_transfers ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_transfer_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_transfer_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_stock_transfers ON stock_transfers;
DROP POLICY IF EXISTS rls_stock_transfer_items ON stock_transfer_items;
DROP POLICY IF EXISTS rls_stock_transfer_sequences ON stock_transfer_sequences;

CREATE POLICY rls_stock_transfers ON stock_transfers
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_stock_transfer_items ON stock_transfer_items
    FOR ALL USING (stock_transfer_id IN (
        SELECT id FROM stock_transfers WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_stock_transfer_sequences ON stock_transfer_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_st_tenant_status ON stock_transfers(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_st_tenant_date ON stock_transfers(tenant_id, transfer_date);
CREATE INDEX IF NOT EXISTS idx_st_number ON stock_transfers(tenant_id, transfer_number);
CREATE INDEX IF NOT EXISTS idx_st_from_warehouse ON stock_transfers(from_warehouse_id);
CREATE INDEX IF NOT EXISTS idx_st_to_warehouse ON stock_transfers(to_warehouse_id);
CREATE INDEX IF NOT EXISTS idx_st_in_transit ON stock_transfers(tenant_id, status) WHERE status = 'in_transit';

CREATE INDEX IF NOT EXISTS idx_sti_transfer ON stock_transfer_items(stock_transfer_id);
CREATE INDEX IF NOT EXISTS idx_sti_item ON stock_transfer_items(item_id);

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

-- Generate transfer number: ST-YYMM-0001
CREATE OR REPLACE FUNCTION generate_stock_transfer_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'ST'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_st_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO stock_transfer_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = stock_transfer_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_st_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_st_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_stock_transfer_number IS 'Generates sequential stock transfer number per tenant per month';

-- Ship stock transfer - reduce from_warehouse stock
CREATE OR REPLACE FUNCTION ship_stock_transfer(
    p_transfer_id UUID,
    p_shipped_by UUID
) RETURNS TABLE(success BOOLEAN, message TEXT) AS $$
DECLARE
    v_transfer RECORD;
    v_item RECORD;
    v_tenant_id TEXT;
    v_available DECIMAL;
BEGIN
    -- Get transfer
    SELECT * INTO v_transfer
    FROM stock_transfers
    WHERE id = p_transfer_id FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'Transfer not found'::TEXT;
        RETURN;
    END IF;

    v_tenant_id := v_transfer.tenant_id;

    IF v_transfer.status != 'draft' THEN
        RETURN QUERY SELECT false, ('Cannot ship transfer with status: ' || v_transfer.status)::TEXT;
        RETURN;
    END IF;

    -- Check stock availability for each item
    FOR v_item IN
        SELECT * FROM stock_transfer_items WHERE stock_transfer_id = p_transfer_id
    LOOP
        v_available := get_available_stock(v_tenant_id, v_transfer.from_warehouse_id, v_item.item_id);

        IF v_available < v_item.quantity_requested THEN
            RETURN QUERY SELECT false,
                ('Insufficient stock for ' || v_item.item_name || ': available=' || v_available || ', requested=' || v_item.quantity_requested)::TEXT;
            RETURN;
        END IF;
    END LOOP;

    -- Reduce stock from source warehouse via inventory_ledger
    FOR v_item IN
        SELECT * FROM stock_transfer_items WHERE stock_transfer_id = p_transfer_id
    LOOP
        -- Insert negative quantity to inventory_ledger
        INSERT INTO inventory_ledger (
            tenant_id, item_id, warehouse_id,
            quantity_change, unit_cost, total_value,
            source_type, source_id,
            transaction_date, created_at
        ) VALUES (
            v_tenant_id, v_item.item_id, v_transfer.from_warehouse_id,
            -v_item.quantity_requested, v_item.unit_cost, -(v_item.quantity_requested * v_item.unit_cost),
            'STOCK_TRANSFER_OUT', p_transfer_id,
            v_transfer.transfer_date, NOW()
        );

        -- Update shipped quantity
        UPDATE stock_transfer_items
        SET quantity_shipped = v_item.quantity_requested
        WHERE id = v_item.id;
    END LOOP;

    -- Update transfer status
    UPDATE stock_transfers
    SET status = 'in_transit',
        shipped_date = CURRENT_DATE,
        shipped_by = p_shipped_by,
        updated_at = NOW()
    WHERE id = p_transfer_id;

    RETURN QUERY SELECT true, 'Transfer shipped successfully'::TEXT;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION ship_stock_transfer IS 'Ships a stock transfer - reduces from_warehouse stock';

-- Receive stock transfer - increase to_warehouse stock
CREATE OR REPLACE FUNCTION receive_stock_transfer(
    p_transfer_id UUID,
    p_received_by UUID,
    p_items JSONB DEFAULT NULL -- Optional: [{item_id, quantity_received}] for partial receive
) RETURNS TABLE(success BOOLEAN, message TEXT) AS $$
DECLARE
    v_transfer RECORD;
    v_item RECORD;
    v_tenant_id TEXT;
    v_received_qty DECIMAL;
BEGIN
    -- Get transfer
    SELECT * INTO v_transfer
    FROM stock_transfers
    WHERE id = p_transfer_id FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'Transfer not found'::TEXT;
        RETURN;
    END IF;

    v_tenant_id := v_transfer.tenant_id;

    IF v_transfer.status != 'in_transit' THEN
        RETURN QUERY SELECT false, ('Cannot receive transfer with status: ' || v_transfer.status)::TEXT;
        RETURN;
    END IF;

    -- Add stock to destination warehouse
    FOR v_item IN
        SELECT * FROM stock_transfer_items WHERE stock_transfer_id = p_transfer_id
    LOOP
        -- Determine received quantity
        IF p_items IS NOT NULL THEN
            SELECT (elem->>'quantity_received')::DECIMAL INTO v_received_qty
            FROM jsonb_array_elements(p_items) elem
            WHERE (elem->>'item_id')::UUID = v_item.item_id;

            IF v_received_qty IS NULL THEN
                v_received_qty := v_item.quantity_shipped;
            END IF;
        ELSE
            v_received_qty := v_item.quantity_shipped;
        END IF;

        -- Insert positive quantity to inventory_ledger
        INSERT INTO inventory_ledger (
            tenant_id, item_id, warehouse_id,
            quantity_change, unit_cost, total_value,
            source_type, source_id,
            transaction_date, created_at
        ) VALUES (
            v_tenant_id, v_item.item_id, v_transfer.to_warehouse_id,
            v_received_qty, v_item.unit_cost, (v_received_qty * v_item.unit_cost),
            'STOCK_TRANSFER_IN', p_transfer_id,
            CURRENT_DATE, NOW()
        );

        -- Update received quantity
        UPDATE stock_transfer_items
        SET quantity_received = v_received_qty
        WHERE id = v_item.id;
    END LOOP;

    -- Update transfer status
    UPDATE stock_transfers
    SET status = 'received',
        received_date = CURRENT_DATE,
        received_by = p_received_by,
        updated_at = NOW()
    WHERE id = p_transfer_id;

    RETURN QUERY SELECT true, 'Transfer received successfully'::TEXT;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION receive_stock_transfer IS 'Receives a stock transfer - increases to_warehouse stock';

-- ============================================================================
-- 7. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_stock_transfers_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stock_transfers_updated_at ON stock_transfers;
CREATE TRIGGER trg_stock_transfers_updated_at
    BEFORE UPDATE ON stock_transfers
    FOR EACH ROW EXECUTE FUNCTION update_stock_transfers_updated_at();

-- Auto-calculate transfer totals
CREATE OR REPLACE FUNCTION update_stock_transfer_totals()
RETURNS TRIGGER AS $$
DECLARE
    v_st_id UUID;
    v_total_items INT;
    v_total_qty DECIMAL;
    v_total_val BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_st_id := OLD.stock_transfer_id;
    ELSE
        v_st_id := NEW.stock_transfer_id;
    END IF;

    SELECT
        COUNT(*),
        COALESCE(SUM(quantity_requested), 0),
        COALESCE(SUM(total_value), 0)
    INTO v_total_items, v_total_qty, v_total_val
    FROM stock_transfer_items
    WHERE stock_transfer_id = v_st_id;

    UPDATE stock_transfers
    SET total_items = v_total_items,
        total_quantity = v_total_qty,
        total_value = v_total_val,
        updated_at = NOW()
    WHERE id = v_st_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_st_totals ON stock_transfer_items;
CREATE TRIGGER trg_update_st_totals
    AFTER INSERT OR UPDATE OR DELETE ON stock_transfer_items
    FOR EACH ROW EXECUTE FUNCTION update_stock_transfer_totals();

-- Prevent modification of shipped/received transfers
CREATE OR REPLACE FUNCTION prevent_st_modification()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IN ('in_transit', 'received') AND TG_OP = 'UPDATE' THEN
        -- Allow only status changes and receiving
        IF NEW.status != OLD.status OR
           NEW.received_by IS DISTINCT FROM OLD.received_by OR
           NEW.received_date IS DISTINCT FROM OLD.received_date THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION 'Cannot modify stock transfer after shipping';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_st_modification ON stock_transfers;
CREATE TRIGGER trg_prevent_st_modification
    BEFORE UPDATE ON stock_transfers
    FOR EACH ROW EXECUTE FUNCTION prevent_st_modification();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V044: Stock Transfers created successfully';
    RAISE NOTICE 'Tables: stock_transfers, stock_transfer_items, stock_transfer_sequences';
    RAISE NOTICE 'Functions: generate_stock_transfer_number, ship_stock_transfer, receive_stock_transfer';
    RAISE NOTICE 'IMPORTANT: Stock transfers do NOT create journal entries';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
