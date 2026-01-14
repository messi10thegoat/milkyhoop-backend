-- V026: Sales Invoices Module (Faktur Penjualan)
-- Creates tables for sales invoice management with accounting kernel integration
-- Mirrors bills module structure but for sales (AR instead of AP)

-- ============================================================================
-- SALES INVOICES TABLE - Main invoice records
-- ============================================================================
CREATE TABLE IF NOT EXISTS sales_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Invoice details
    invoice_number VARCHAR(50) NOT NULL,
    customer_id UUID REFERENCES customers(id),
    customer_name VARCHAR(255) NOT NULL,

    -- Amounts (using BIGINT for IDR - no decimal needed)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,
    amount_paid BIGINT DEFAULT 0,

    -- Status: draft, posted, partial, paid, overdue, void
    status VARCHAR(20) DEFAULT 'draft',

    -- Dates
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,

    -- Reference
    ref_no VARCHAR(100),  -- Customer PO number or reference
    notes TEXT,

    -- Accounting integration
    ar_id UUID,  -- Link to accounts_receivable
    journal_id UUID,  -- Link to journal_entries

    -- Status tracking
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_sales_invoices_tenant_number UNIQUE(tenant_id, invoice_number),
    CONSTRAINT chk_sales_invoice_status CHECK (status IN ('draft', 'posted', 'partial', 'paid', 'overdue', 'void'))
);

-- ============================================================================
-- INVOICE ITEMS TABLE - Line items for each invoice
-- ============================================================================
CREATE TABLE IF NOT EXISTS sales_invoice_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id) ON DELETE CASCADE,

    -- Product reference
    item_id UUID,  -- Link to items table
    item_code VARCHAR(50),
    description VARCHAR(255) NOT NULL,

    -- Quantities
    quantity DECIMAL(10,2) NOT NULL,
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
    subtotal BIGINT NOT NULL,  -- quantity * unit_price
    total BIGINT NOT NULL,  -- subtotal - discount + tax

    -- Order for display
    line_number INT DEFAULT 1
);

-- ============================================================================
-- INVOICE PAYMENTS TABLE - Payment records for invoices
-- ============================================================================
CREATE TABLE IF NOT EXISTS sales_invoice_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id),

    -- Payment details
    amount BIGINT NOT NULL,
    payment_date DATE NOT NULL,
    payment_method VARCHAR(20) NOT NULL,  -- cash, transfer, check, other

    -- Account reference
    bank_account_id UUID,  -- Link to bank_accounts (if applicable)
    account_id UUID NOT NULL,  -- Kas/Bank account from CoA
    reference VARCHAR(100),  -- Transfer/check reference number
    notes TEXT,

    -- Accounting integration
    journal_id UUID,  -- Payment journal entry

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL
);

-- ============================================================================
-- INVOICE NUMBER SEQUENCE TABLE - For auto-generating invoice numbers
-- ============================================================================
CREATE TABLE IF NOT EXISTS sales_invoice_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,  -- Format: YYYY-MM
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'INV',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_sales_inv_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_sales_inv_tenant_status ON sales_invoices(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_sales_inv_tenant_due_date ON sales_invoices(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_sales_inv_customer ON sales_invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_sales_inv_customer_name ON sales_invoices(tenant_id, customer_name);
CREATE INDEX IF NOT EXISTS idx_sales_inv_number ON sales_invoices(tenant_id, invoice_number);
CREATE INDEX IF NOT EXISTS idx_sales_inv_created_at ON sales_invoices(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_inv_items_invoice ON sales_invoice_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_sales_inv_payments_invoice ON sales_invoice_payments(invoice_id);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE sales_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_invoice_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_invoice_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_invoice_sequences ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY rls_sales_invoices ON sales_invoices
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_sales_invoice_items ON sales_invoice_items
    FOR ALL USING (invoice_id IN (
        SELECT id FROM sales_invoices WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_sales_invoice_payments ON sales_invoice_payments
    FOR ALL USING (invoice_id IN (
        SELECT id FROM sales_invoices WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_sales_invoice_sequences ON sales_invoice_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Generate next invoice number
CREATE OR REPLACE FUNCTION generate_sales_invoice_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'INV'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_invoice_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    -- Upsert sequence and get next number
    INSERT INTO sales_invoice_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = sales_invoice_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: INV-YYMM-0001
    v_invoice_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_invoice_number;
END;
$$ LANGUAGE plpgsql;

-- Calculate invoice totals
CREATE OR REPLACE FUNCTION calculate_sales_invoice_totals(p_invoice_id UUID)
RETURNS TABLE(
    subtotal BIGINT,
    discount_amount BIGINT,
    tax_amount BIGINT,
    total_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(sii.subtotal), 0)::BIGINT as subtotal,
        COALESCE(SUM(sii.discount_amount), 0)::BIGINT as discount_amount,
        COALESCE(SUM(sii.tax_amount), 0)::BIGINT as tax_amount,
        COALESCE(SUM(sii.total), 0)::BIGINT as total_amount
    FROM sales_invoice_items sii
    WHERE sii.invoice_id = p_invoice_id;
END;
$$ LANGUAGE plpgsql;

-- Update invoice status based on payments
CREATE OR REPLACE FUNCTION update_sales_invoice_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_paid BIGINT;
    v_total_amount BIGINT;
    v_due_date DATE;
    v_new_status VARCHAR(20);
BEGIN
    -- Get invoice details
    SELECT total_amount, due_date INTO v_total_amount, v_due_date
    FROM sales_invoices
    WHERE id = NEW.invoice_id;

    -- Calculate total paid
    SELECT COALESCE(SUM(amount), 0) INTO v_total_paid
    FROM sales_invoice_payments
    WHERE invoice_id = NEW.invoice_id;

    -- Determine new status
    IF v_total_paid >= v_total_amount THEN
        v_new_status := 'paid';
    ELSIF v_total_paid > 0 THEN
        v_new_status := 'partial';
    ELSIF v_due_date < CURRENT_DATE THEN
        v_new_status := 'overdue';
    ELSE
        v_new_status := 'posted';
    END IF;

    -- Update invoice
    UPDATE sales_invoices
    SET amount_paid = v_total_paid,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = NEW.invoice_id
      AND status NOT IN ('draft', 'void');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_sales_invoice_on_payment
    AFTER INSERT OR UPDATE OR DELETE ON sales_invoice_payments
    FOR EACH ROW EXECUTE FUNCTION update_sales_invoice_status();

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_sales_invoices_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sales_invoices_updated_at
    BEFORE UPDATE ON sales_invoices
    FOR EACH ROW EXECUTE FUNCTION trigger_sales_invoices_updated_at();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE sales_invoices IS 'Faktur Penjualan - Sales Invoice records';
COMMENT ON COLUMN sales_invoices.status IS 'draft = belum post, posted = sudah post ke accounting, partial = dibayar sebagian, paid = lunas, void = dibatalkan';
COMMENT ON COLUMN sales_invoices.ar_id IS 'Link ke accounts_receivable untuk tracking piutang';
COMMENT ON COLUMN sales_invoices.journal_id IS 'Link ke journal_entries untuk audit trail';
