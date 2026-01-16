-- ============================================================================
-- V045: Sales Receipts (POS/Cash Sales - Bukti Penjualan)
-- ============================================================================
-- Purpose: Immediate cash/card sales transactions (like POS)
-- Tables: sales_receipts, sales_receipt_items, sales_receipt_sequences
-- Creates TWO journal entries: Sales + COGS
-- ============================================================================

-- ============================================================================
-- 1. SALES RECEIPTS TABLE - POS transactions
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identification
    receipt_number VARCHAR(50) NOT NULL,
    receipt_date DATE NOT NULL,
    receipt_time TIME,

    -- Customer (optional for walk-in)
    customer_id VARCHAR(255) REFERENCES customers(id),
    customer_name VARCHAR(255),
    customer_phone VARCHAR(50),
    customer_email VARCHAR(100),

    -- Location
    warehouse_id UUID REFERENCES warehouses(id),

    -- Amounts
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL DEFAULT 0,

    -- Payment
    payment_method VARCHAR(50) DEFAULT 'cash', -- cash, card, transfer, qris, gopay, ovo, dana
    payment_reference VARCHAR(100),
    amount_received BIGINT NOT NULL DEFAULT 0,
    change_amount BIGINT DEFAULT 0,

    -- Bank account (for non-cash payments)
    bank_account_id UUID,

    -- Journals
    journal_id UUID REFERENCES journal_entries(id),
    cogs_journal_id UUID REFERENCES journal_entries(id),

    -- Status
    status VARCHAR(20) DEFAULT 'completed', -- completed, void

    -- Cashier/POS info
    cashier_id UUID,
    cashier_name VARCHAR(100),
    pos_terminal VARCHAR(50),
    shift_number VARCHAR(50),

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,

    -- Notes
    notes TEXT,
    internal_notes TEXT,

    CONSTRAINT uq_sales_receipts_number UNIQUE(tenant_id, receipt_number),
    CONSTRAINT chk_sr_status CHECK (status IN ('completed', 'void')),
    CONSTRAINT chk_sr_payment CHECK (payment_method IN ('cash', 'card', 'transfer', 'qris', 'gopay', 'ovo', 'dana', 'other'))
);

COMMENT ON TABLE sales_receipts IS 'POS/Cash sales transactions with immediate payment';
COMMENT ON COLUMN sales_receipts.payment_method IS 'Payment type: cash, card, transfer, qris, gopay, ovo, dana, other';

-- ============================================================================
-- 2. SALES RECEIPT ITEMS TABLE - Line items
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_receipt_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_receipt_id UUID NOT NULL REFERENCES sales_receipts(id) ON DELETE CASCADE,

    -- Product
    item_id UUID NOT NULL,
    item_code VARCHAR(50),
    item_name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(50),

    -- Pricing
    unit_price BIGINT NOT NULL,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,

    -- Tax
    tax_id UUID,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,

    -- Line total
    subtotal BIGINT NOT NULL, -- quantity * unit_price
    line_total BIGINT NOT NULL, -- after discount and tax

    -- COGS tracking
    unit_cost BIGINT DEFAULT 0,
    total_cost BIGINT DEFAULT 0, -- quantity * unit_cost

    -- Batch/Serial (optional)
    batch_id UUID,
    batch_number VARCHAR(100),
    serial_ids UUID[],

    line_number INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_sri_qty CHECK (quantity > 0),
    CONSTRAINT chk_sri_price CHECK (unit_price >= 0)
);

COMMENT ON TABLE sales_receipt_items IS 'Line items for sales receipts with COGS tracking';

-- ============================================================================
-- 3. SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS sales_receipt_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'SR',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_sr_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE sales_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_receipt_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_receipt_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_sales_receipts ON sales_receipts;
DROP POLICY IF EXISTS rls_sales_receipt_items ON sales_receipt_items;
DROP POLICY IF EXISTS rls_sales_receipt_sequences ON sales_receipt_sequences;

CREATE POLICY rls_sales_receipts ON sales_receipts
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_sales_receipt_items ON sales_receipt_items
    FOR ALL USING (sales_receipt_id IN (
        SELECT id FROM sales_receipts WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_sales_receipt_sequences ON sales_receipt_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_sr_tenant_date ON sales_receipts(tenant_id, receipt_date);
CREATE INDEX IF NOT EXISTS idx_sr_tenant_status ON sales_receipts(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_sr_number ON sales_receipts(tenant_id, receipt_number);
CREATE INDEX IF NOT EXISTS idx_sr_customer ON sales_receipts(customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sr_warehouse ON sales_receipts(warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sr_payment ON sales_receipts(tenant_id, payment_method);
CREATE INDEX IF NOT EXISTS idx_sr_cashier ON sales_receipts(tenant_id, cashier_id) WHERE cashier_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sr_journal ON sales_receipts(journal_id) WHERE journal_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sri_receipt ON sales_receipt_items(sales_receipt_id);
CREATE INDEX IF NOT EXISTS idx_sri_item ON sales_receipt_items(item_id);

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

-- Generate receipt number: SR-YYMM-0001
CREATE OR REPLACE FUNCTION generate_sales_receipt_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'SR'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_sr_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO sales_receipt_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = sales_receipt_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_sr_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_sr_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_sales_receipt_number IS 'Generates sequential sales receipt number per tenant per month';

-- Get daily sales summary
CREATE OR REPLACE FUNCTION get_daily_sales_summary(
    p_tenant_id TEXT,
    p_date DATE DEFAULT CURRENT_DATE,
    p_warehouse_id UUID DEFAULT NULL
) RETURNS TABLE(
    total_receipts INT,
    total_sales BIGINT,
    total_tax BIGINT,
    total_discount BIGINT,
    cash_amount BIGINT,
    card_amount BIGINT,
    transfer_amount BIGINT,
    qris_amount BIGINT,
    other_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::INT as total_receipts,
        COALESCE(SUM(sr.total_amount), 0)::BIGINT as total_sales,
        COALESCE(SUM(sr.tax_amount), 0)::BIGINT as total_tax,
        COALESCE(SUM(sr.discount_amount), 0)::BIGINT as total_discount,
        COALESCE(SUM(CASE WHEN sr.payment_method = 'cash' THEN sr.total_amount ELSE 0 END), 0)::BIGINT as cash_amount,
        COALESCE(SUM(CASE WHEN sr.payment_method = 'card' THEN sr.total_amount ELSE 0 END), 0)::BIGINT as card_amount,
        COALESCE(SUM(CASE WHEN sr.payment_method = 'transfer' THEN sr.total_amount ELSE 0 END), 0)::BIGINT as transfer_amount,
        COALESCE(SUM(CASE WHEN sr.payment_method IN ('qris', 'gopay', 'ovo', 'dana') THEN sr.total_amount ELSE 0 END), 0)::BIGINT as qris_amount,
        COALESCE(SUM(CASE WHEN sr.payment_method = 'other' THEN sr.total_amount ELSE 0 END), 0)::BIGINT as other_amount
    FROM sales_receipts sr
    WHERE sr.tenant_id = p_tenant_id
    AND sr.receipt_date = p_date
    AND sr.status = 'completed'
    AND (p_warehouse_id IS NULL OR sr.warehouse_id = p_warehouse_id);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_daily_sales_summary IS 'Returns daily sales summary by payment method';

-- ============================================================================
-- 7. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_sales_receipts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sales_receipts_updated_at ON sales_receipts;
CREATE TRIGGER trg_sales_receipts_updated_at
    BEFORE UPDATE ON sales_receipts
    FOR EACH ROW EXECUTE FUNCTION update_sales_receipts_updated_at();

-- Auto-calculate receipt totals
CREATE OR REPLACE FUNCTION update_sales_receipt_totals()
RETURNS TRIGGER AS $$
DECLARE
    v_sr_id UUID;
    v_subtotal BIGINT;
    v_tax BIGINT;
    v_discount BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_sr_id := OLD.sales_receipt_id;
    ELSE
        v_sr_id := NEW.sales_receipt_id;
    END IF;

    SELECT
        COALESCE(SUM(subtotal), 0),
        COALESCE(SUM(tax_amount), 0),
        COALESCE(SUM(discount_amount), 0)
    INTO v_subtotal, v_tax, v_discount
    FROM sales_receipt_items
    WHERE sales_receipt_id = v_sr_id;

    UPDATE sales_receipts
    SET subtotal = v_subtotal,
        tax_amount = v_tax,
        -- Keep header discount_amount if set, otherwise use sum of line discounts
        total_amount = v_subtotal - discount_amount + v_tax,
        updated_at = NOW()
    WHERE id = v_sr_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_sr_totals ON sales_receipt_items;
CREATE TRIGGER trg_update_sr_totals
    AFTER INSERT OR UPDATE OR DELETE ON sales_receipt_items
    FOR EACH ROW EXECUTE FUNCTION update_sales_receipt_totals();

-- Prevent modification of completed receipts
CREATE OR REPLACE FUNCTION prevent_sr_modification()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'completed' AND TG_OP = 'UPDATE' THEN
        -- Allow only void operation
        IF NEW.status = 'void' THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION 'Cannot modify completed sales receipt. Use void instead.';
    END IF;

    IF OLD.status = 'void' THEN
        RAISE EXCEPTION 'Cannot modify voided sales receipt';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_sr_modification ON sales_receipts;
CREATE TRIGGER trg_prevent_sr_modification
    BEFORE UPDATE ON sales_receipts
    FOR EACH ROW EXECUTE FUNCTION prevent_sr_modification();

-- ============================================================================
-- 8. JOURNAL ENTRY NOTES (Implementation in router)
-- ============================================================================

/*
Sales Receipt creates TWO journal entries:

1. SALES JOURNAL (on create):
   For CASH payment:
   Dr. Kas (1-10100)              total_amount
       Cr. Penjualan (4-10100)        subtotal
       Cr. PPN Keluaran (2-10300)     tax_amount

   For NON-CASH payment (card, transfer, qris):
   Dr. Bank (1-10200)             total_amount
       Cr. Penjualan (4-10100)        subtotal
       Cr. PPN Keluaran (2-10300)     tax_amount

2. COGS JOURNAL (for inventory items):
   Dr. HPP (5-10100)              total_cost
       Cr. Persediaan (1-10400)       total_cost

3. VOID REVERSAL:
   Creates reversing entries for both journals
*/

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V045: Sales Receipts (POS) created successfully';
    RAISE NOTICE 'Tables: sales_receipts, sales_receipt_items, sales_receipt_sequences';
    RAISE NOTICE 'Functions: generate_sales_receipt_number, get_daily_sales_summary';
    RAISE NOTICE 'Payment methods: cash, card, transfer, qris, gopay, ovo, dana, other';
    RAISE NOTICE 'Creates 2 journals: Sales + COGS';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
