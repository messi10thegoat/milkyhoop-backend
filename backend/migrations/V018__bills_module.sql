-- V018: Bills Module (Faktur Pembelian)
-- Creates tables for bill management with accounting kernel integration

-- ============================================================================
-- BILLS TABLE - Main bill/invoice records
-- ============================================================================
CREATE TABLE IF NOT EXISTS bills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Link to source transaction (optional - for imported bills)
    transaction_id TEXT REFERENCES transaksi_harian(id),

    -- Bill details
    invoice_number VARCHAR(50) NOT NULL,
    vendor_id UUID,  -- Optional link to suppliers table
    vendor_name VARCHAR(255) NOT NULL,

    -- Amounts (using INTEGER for IDR - no decimal needed)
    amount BIGINT NOT NULL,
    amount_paid BIGINT DEFAULT 0,

    -- Status: unpaid, partial, paid, overdue, void
    status VARCHAR(20) DEFAULT 'unpaid',

    -- Dates
    issue_date DATE NOT NULL,
    due_date DATE NOT NULL,

    -- Notes & metadata
    notes TEXT,
    voided_at TIMESTAMPTZ,
    voided_reason TEXT,

    -- Accounting integration
    ap_id UUID,  -- Link to accounts_payable
    journal_id UUID,  -- Link to journal_entries

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_bills_tenant_invoice UNIQUE(tenant_id, invoice_number)
);

-- ============================================================================
-- BILL ITEMS TABLE - Line items for each bill
-- ============================================================================
CREATE TABLE IF NOT EXISTS bill_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id UUID NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    product_id UUID,  -- Optional link to persediaan
    description VARCHAR(255),
    quantity DECIMAL(10,2) NOT NULL,
    unit VARCHAR(20),
    unit_price BIGINT NOT NULL,
    subtotal BIGINT NOT NULL,

    -- Order for display
    line_number INT DEFAULT 1
);

-- ============================================================================
-- BILL PAYMENTS TABLE - Payment records for bills
-- ============================================================================
CREATE TABLE IF NOT EXISTS bill_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id UUID NOT NULL REFERENCES bills(id),
    amount BIGINT NOT NULL,
    payment_date DATE NOT NULL,
    payment_method VARCHAR(20) NOT NULL,  -- cash, transfer, check, other
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
-- BILL ATTACHMENTS TABLE - File attachments for bills
-- ============================================================================
CREATE TABLE IF NOT EXISTS bill_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id UUID NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER,
    mime_type VARCHAR(100),
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    uploaded_by UUID NOT NULL
);

-- ============================================================================
-- BILL NUMBER SEQUENCE TABLE - For auto-generating invoice numbers
-- ============================================================================
CREATE TABLE IF NOT EXISTS bill_number_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,  -- Format: YYYY-MM
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'BILL',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_bill_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_bills_tenant_status ON bills(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_bills_tenant_due_date ON bills(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_bills_vendor ON bills(vendor_id);
CREATE INDEX IF NOT EXISTS idx_bills_vendor_name ON bills(tenant_id, vendor_name);
CREATE INDEX IF NOT EXISTS idx_bills_invoice_number ON bills(tenant_id, invoice_number);
CREATE INDEX IF NOT EXISTS idx_bills_created_at ON bills(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_payments_bill ON bill_payments(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_attachments_bill ON bill_attachments(bill_id);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE bills ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_number_sequences ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY rls_bills ON bills
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bill_items ON bill_items
    FOR ALL USING (bill_id IN (
        SELECT id FROM bills WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_bill_payments ON bill_payments
    FOR ALL USING (bill_id IN (
        SELECT id FROM bills WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_bill_attachments ON bill_attachments
    FOR ALL USING (bill_id IN (
        SELECT id FROM bills WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_bill_number_sequences ON bill_number_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- HELPER FUNCTION: Generate next bill number
-- ============================================================================
CREATE OR REPLACE FUNCTION generate_bill_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'BILL'
)
RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_bill_number VARCHAR(50);
BEGIN
    -- Get current year-month
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    -- Insert or update sequence (atomic)
    INSERT INTO bill_number_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET
        last_number = bill_number_sequences.last_number + 1,
        updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: BILL-2501-0001
    v_bill_number := p_prefix || '-' ||
                     SUBSTRING(v_year_month, 3, 2) ||
                     SUBSTRING(v_year_month, 6, 2) || '-' ||
                     LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_bill_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- HELPER FUNCTION: Calculate bill status based on payments and due date
-- ============================================================================
CREATE OR REPLACE FUNCTION calculate_bill_status(
    p_amount BIGINT,
    p_amount_paid BIGINT,
    p_due_date DATE,
    p_current_status VARCHAR
)
RETURNS VARCHAR AS $$
BEGIN
    -- If voided, keep status
    IF p_current_status = 'void' THEN
        RETURN 'void';
    END IF;

    -- Calculate based on payment
    IF p_amount_paid >= p_amount THEN
        RETURN 'paid';
    ELSIF p_amount_paid > 0 THEN
        -- Partial payment - check if overdue
        IF p_due_date < CURRENT_DATE THEN
            RETURN 'overdue';
        ELSE
            RETURN 'partial';
        END IF;
    ELSE
        -- No payment
        IF p_due_date < CURRENT_DATE THEN
            RETURN 'overdue';
        ELSE
            RETURN 'unpaid';
        END IF;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- TRIGGER: Auto-update bill status after payment
-- ============================================================================
CREATE OR REPLACE FUNCTION update_bill_on_payment()
RETURNS TRIGGER AS $$
DECLARE
    v_total_paid BIGINT;
    v_bill_amount BIGINT;
    v_bill_due_date DATE;
    v_current_status VARCHAR;
    v_new_status VARCHAR;
BEGIN
    -- Get current bill info
    SELECT amount, due_date, status
    INTO v_bill_amount, v_bill_due_date, v_current_status
    FROM bills WHERE id = NEW.bill_id;

    -- Calculate total paid
    SELECT COALESCE(SUM(amount), 0) INTO v_total_paid
    FROM bill_payments WHERE bill_id = NEW.bill_id;

    -- Calculate new status
    v_new_status := calculate_bill_status(
        v_bill_amount, v_total_paid, v_bill_due_date, v_current_status
    );

    -- Update bill
    UPDATE bills
    SET amount_paid = v_total_paid,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = NEW.bill_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_bill_on_payment
    AFTER INSERT ON bill_payments
    FOR EACH ROW
    EXECUTE FUNCTION update_bill_on_payment();

-- ============================================================================
-- COMMENT DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE bills IS 'Faktur pembelian dari vendor/supplier';
COMMENT ON TABLE bill_items IS 'Detail item dalam faktur pembelian';
COMMENT ON TABLE bill_payments IS 'Catatan pembayaran untuk faktur';
COMMENT ON TABLE bill_attachments IS 'Lampiran file (nota, struk, dll)';
COMMENT ON TABLE bill_number_sequences IS 'Sequence untuk auto-generate nomor faktur';

COMMENT ON COLUMN bills.status IS 'unpaid=belum bayar, partial=sebagian, paid=lunas, overdue=jatuh tempo, void=dibatalkan';
COMMENT ON COLUMN bills.ap_id IS 'Link ke accounts_payable di accounting kernel';
COMMENT ON COLUMN bills.journal_id IS 'Link ke journal_entries di accounting kernel';
COMMENT ON COLUMN bill_payments.account_id IS 'UUID akun Kas/Bank dari chart_of_accounts';
