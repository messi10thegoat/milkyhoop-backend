-- ============================================================================
-- V069: Expenses Module (Biaya & Pengeluaran)
-- Creates tables for expense management with auto journal posting
-- Supports single and itemized expenses
-- ============================================================================

-- ============================================================================
-- EXPENSES TABLE - Main expense records
-- ============================================================================
CREATE TABLE IF NOT EXISTS expenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Numbering
    expense_number VARCHAR(50) NOT NULL,

    -- Dates
    expense_date DATE NOT NULL,

    -- Payment source (Kas/Bank)
    paid_through_id UUID NOT NULL,  -- FK to bank_accounts
    paid_through_name VARCHAR(255),
    paid_through_coa_id UUID,       -- FK to chart_of_accounts (for journal)

    -- Vendor (optional)
    vendor_id UUID,
    vendor_name VARCHAR(255),

    -- Single expense mode (for non-itemized)
    account_id UUID,                -- FK to chart_of_accounts
    account_name VARCHAR(255),

    -- Amounts (BIGINT for Rupiah)
    currency VARCHAR(3) DEFAULT 'IDR',
    subtotal BIGINT NOT NULL DEFAULT 0,

    -- Tax (PPN Masukan)
    tax_id UUID,
    tax_name VARCHAR(50),
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,

    -- PPh Withholding (PPh 21/23/4(2) dipotong)
    pph_type VARCHAR(20),           -- 'PPH_21', 'PPH_23', 'PPH_4_2'
    pph_rate DECIMAL(5,2) DEFAULT 0,
    pph_amount BIGINT DEFAULT 0,

    -- Total
    total_amount BIGINT NOT NULL DEFAULT 0,

    -- Mode
    is_itemized BOOLEAN DEFAULT false,

    -- Status
    status VARCHAR(20) DEFAULT 'posted',  -- draft, posted, void

    -- Billable expense tracking
    is_billable BOOLEAN DEFAULT false,
    billed_to_customer_id UUID,
    billed_invoice_id UUID,

    -- Reference & notes
    reference VARCHAR(100),         -- Nomor kwitansi/nota
    notes TEXT,
    has_receipt BOOLEAN DEFAULT false,

    -- Accounting link
    journal_id UUID,

    -- Audit
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT chk_expense_amount CHECK (total_amount >= 0),
    CONSTRAINT chk_expense_status CHECK (status IN ('draft', 'posted', 'void')),
    CONSTRAINT chk_expense_pph_type CHECK (pph_type IS NULL OR pph_type IN ('PPH_21', 'PPH_23', 'PPH_4_2')),
    CONSTRAINT uq_expenses_tenant_number UNIQUE(tenant_id, expense_number)
);

-- ============================================================================
-- EXPENSE ITEMS TABLE - Line items for itemized expenses
-- ============================================================================
CREATE TABLE IF NOT EXISTS expense_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    expense_id UUID NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,

    -- Account
    account_id UUID NOT NULL,       -- FK to chart_of_accounts
    account_name VARCHAR(255),

    -- Amount
    amount BIGINT NOT NULL DEFAULT 0,

    -- Description
    notes TEXT,

    -- Order
    line_number INT DEFAULT 1,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- EXPENSE ATTACHMENTS TABLE - Receipts and documents
-- ============================================================================
CREATE TABLE IF NOT EXISTS expense_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    expense_id UUID NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,

    file_name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT,
    mime_type VARCHAR(100),

    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    uploaded_by UUID
);

-- ============================================================================
-- EXPENSE NUMBER SEQUENCES TABLE - For auto-generating expense numbers
-- ============================================================================
CREATE TABLE IF NOT EXISTS expense_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL UNIQUE,
    prefix VARCHAR(10) DEFAULT 'EXP',
    last_number INT DEFAULT 0,
    last_reset_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_expenses_tenant ON expenses(tenant_id);
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_date ON expenses(tenant_id, expense_date);
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_status ON expenses(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_vendor ON expenses(tenant_id, vendor_id) WHERE vendor_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_account ON expenses(tenant_id, account_id) WHERE account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_expenses_created_at ON expenses(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_expenses_billable ON expenses(tenant_id, is_billable) WHERE is_billable = true;

CREATE INDEX IF NOT EXISTS idx_expense_items_expense ON expense_items(expense_id);
CREATE INDEX IF NOT EXISTS idx_expense_items_account ON expense_items(account_id);

CREATE INDEX IF NOT EXISTS idx_expense_attachments_expense ON expense_attachments(expense_id);

-- Full-text search for Indonesian
CREATE INDEX IF NOT EXISTS idx_expenses_search ON expenses
    USING gin(to_tsvector('indonesian',
        expense_number || ' ' || COALESCE(vendor_name, '') || ' ' || COALESCE(notes, '')));

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE expenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE expense_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE expense_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE expense_sequences ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_expenses ON expenses
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_expense_items ON expense_items
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_expense_attachments ON expense_attachments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_expense_sequences ON expense_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_expenses_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_expenses_updated_at
    BEFORE UPDATE ON expenses
    FOR EACH ROW EXECUTE FUNCTION trigger_expenses_updated_at();

-- ============================================================================
-- FUNCTION: Generate expense number
-- Format: EXP-YYMM-0001
-- ============================================================================
CREATE OR REPLACE FUNCTION generate_expense_number(p_tenant_id TEXT)
RETURNS VARCHAR(50) AS $$
DECLARE
    v_prefix VARCHAR(10);
    v_number INT;
    v_year_month VARCHAR(4);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYMM');

    -- Insert or update sequence (atomic)
    INSERT INTO expense_sequences (tenant_id, prefix, last_number, last_reset_date)
    VALUES (p_tenant_id, 'EXP', 1, CURRENT_DATE)
    ON CONFLICT (tenant_id) DO UPDATE
    SET last_number = CASE
        WHEN TO_CHAR(expense_sequences.last_reset_date, 'YYMM') != v_year_month
        THEN 1
        ELSE expense_sequences.last_number + 1
    END,
    last_reset_date = CURRENT_DATE
    RETURNING prefix, last_number INTO v_prefix, v_number;

    -- Format: EXP-2601-0001
    RETURN v_prefix || '-' || v_year_month || '-' || LPAD(v_number::TEXT, 4, '0');
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: Calculate expense totals
-- ============================================================================
CREATE OR REPLACE FUNCTION calculate_expense_totals(
    p_subtotal BIGINT,
    p_tax_rate DECIMAL(5,2),
    p_pph_rate DECIMAL(5,2)
)
RETURNS TABLE (
    tax_amount BIGINT,
    pph_amount BIGINT,
    total_amount BIGINT
) AS $$
DECLARE
    v_tax_amount BIGINT;
    v_pph_amount BIGINT;
    v_total_amount BIGINT;
BEGIN
    -- Calculate PPN Masukan
    v_tax_amount := ROUND(p_subtotal * COALESCE(p_tax_rate, 0) / 100)::BIGINT;

    -- Calculate PPh withheld (reduces payable)
    v_pph_amount := ROUND(p_subtotal * COALESCE(p_pph_rate, 0) / 100)::BIGINT;

    -- Total = Subtotal + PPN - PPh
    v_total_amount := p_subtotal + v_tax_amount - v_pph_amount;

    RETURN QUERY SELECT v_tax_amount, v_pph_amount, v_total_amount;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- FUNCTION: Validate expense status transition
-- ============================================================================
CREATE OR REPLACE FUNCTION validate_expense_status_transition(
    p_current_status VARCHAR,
    p_new_status VARCHAR
)
RETURNS BOOLEAN AS $$
BEGIN
    -- Valid transitions:
    -- draft -> posted (when posting)
    -- draft -> void (cancel draft)
    -- posted -> void (void with reversal)
    -- void -> (no transitions)

    IF p_current_status = p_new_status THEN
        RETURN true;  -- No change
    END IF;

    RETURN CASE p_current_status
        WHEN 'draft' THEN p_new_status IN ('posted', 'void')
        WHEN 'posted' THEN p_new_status = 'void'
        WHEN 'void' THEN false
        ELSE false
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- TRIGGER: Status transition validation
-- ============================================================================
CREATE OR REPLACE FUNCTION trg_validate_expense_status()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    IF NOT validate_expense_status_transition(OLD.status, NEW.status) THEN
        RAISE EXCEPTION 'Invalid expense status transition from % to %',
            OLD.status, NEW.status;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_expenses_status_validation
    BEFORE UPDATE ON expenses
    FOR EACH ROW
    EXECUTE FUNCTION trg_validate_expense_status();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE expenses IS 'Biaya & Pengeluaran - tracks company expenses with auto journal posting';
COMMENT ON COLUMN expenses.expense_number IS 'Auto-generated number: EXP-YYMM-0001';
COMMENT ON COLUMN expenses.paid_through_id IS 'Bank account used for payment (FK bank_accounts)';
COMMENT ON COLUMN expenses.account_id IS 'Expense account for single expense mode (FK chart_of_accounts)';
COMMENT ON COLUMN expenses.pph_type IS 'PPh type: PPH_21 (gaji), PPH_23 (jasa), PPH_4_2 (sewa tanah/bangunan)';
COMMENT ON COLUMN expenses.is_itemized IS 'True if expense has multiple line items with different accounts';
COMMENT ON COLUMN expenses.is_billable IS 'True if expense can be billed to customer';
COMMENT ON COLUMN expenses.status IS 'Status: draft (editable), posted (in accounting), void (cancelled)';

COMMENT ON TABLE expense_items IS 'Line items for itemized expenses - each with different expense account';
COMMENT ON COLUMN expense_items.account_id IS 'Expense account for this line item (FK chart_of_accounts)';

COMMENT ON FUNCTION generate_expense_number IS 'Generates expense number in format EXP-YYMM-0001';
COMMENT ON FUNCTION calculate_expense_totals IS 'Calculates tax, PPh, and total from subtotal';
