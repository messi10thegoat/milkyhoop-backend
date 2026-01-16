-- ============================================================================
-- V030: Credit Notes Module (Nota Kredit / Sales Returns)
-- ============================================================================
-- Purpose: Credit notes for handling sales returns and AR adjustments
-- Creates tables: credit_notes, credit_note_items, credit_note_applications,
--                 credit_note_refunds, credit_note_sequences
-- ============================================================================

-- ============================================================================
-- 1. VERIFY/CREATE REQUIRED ACCOUNTS
-- ============================================================================

-- Add Sales Returns account (4-10300) if not exists
INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active, is_system)
SELECT
    t.tenant_id::uuid,
    '4-10300',
    'Retur Penjualan',
    'INCOME',
    'DEBIT',  -- Contra revenue account
    (SELECT id FROM chart_of_accounts c2 WHERE c2.tenant_id = t.tenant_id::uuid AND c2.code = '4-00000' LIMIT 1),
    true,
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE code = '4-10300' AND tenant_id = t.tenant_id::uuid
);

-- ============================================================================
-- 2. CREDIT NOTES TABLE - Main records
-- ============================================================================

CREATE TABLE IF NOT EXISTS credit_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Credit note identification
    credit_note_number VARCHAR(50) NOT NULL,

    -- Customer reference
    customer_id UUID REFERENCES customers(id),
    customer_name VARCHAR(255) NOT NULL,

    -- Original invoice reference (optional)
    original_invoice_id UUID REFERENCES sales_invoices(id),
    original_invoice_number VARCHAR(50),

    -- Amounts (BIGINT for IDR, stored in smallest unit)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,
    amount_applied BIGINT DEFAULT 0,    -- Amount applied to invoices
    amount_refunded BIGINT DEFAULT 0,   -- Amount refunded as cash

    -- Status workflow: draft -> posted -> partial/applied/void
    -- draft: Not yet posted to accounting
    -- posted: Posted to accounting, available for application
    -- partial: Partially applied
    -- applied: Fully applied or refunded
    -- void: Cancelled
    status VARCHAR(20) DEFAULT 'draft',

    -- Dates
    credit_note_date DATE NOT NULL,

    -- Reason for credit note
    reason VARCHAR(30) NOT NULL,  -- return, pricing_error, discount, damaged, other
    reason_detail TEXT,

    -- Reference
    ref_no VARCHAR(100),
    notes TEXT,

    -- Accounting integration
    ar_id UUID,                          -- Link to accounts_receivable
    journal_id UUID,                     -- Link to journal_entries

    -- Status tracking timestamps
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    voided_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_credit_notes_tenant_number UNIQUE(tenant_id, credit_note_number),
    CONSTRAINT chk_credit_note_status CHECK (status IN ('draft', 'posted', 'partial', 'applied', 'void')),
    CONSTRAINT chk_credit_note_reason CHECK (reason IN ('return', 'pricing_error', 'discount', 'damaged', 'other'))
);

COMMENT ON TABLE credit_notes IS 'Nota Kredit - Credit Notes for sales returns and AR adjustments';
COMMENT ON COLUMN credit_notes.status IS 'draft=not posted, posted=available for use, partial=partially applied, applied=fully used, void=cancelled';
COMMENT ON COLUMN credit_notes.reason IS 'return=goods returned, pricing_error=incorrect price, discount=additional discount, damaged=damaged goods, other=other reason';

-- ============================================================================
-- 3. CREDIT NOTE ITEMS TABLE - Line items
-- ============================================================================

CREATE TABLE IF NOT EXISTS credit_note_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credit_note_id UUID NOT NULL REFERENCES credit_notes(id) ON DELETE CASCADE,

    -- Product reference
    item_id UUID,
    item_code VARCHAR(50),
    description VARCHAR(500) NOT NULL,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(20),
    unit_price BIGINT NOT NULL,

    -- Original invoice item reference
    original_invoice_item_id UUID,

    -- Discounts
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,

    -- Tax
    tax_code VARCHAR(20),
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,

    -- Totals
    subtotal BIGINT NOT NULL,           -- quantity * unit_price
    total BIGINT NOT NULL,              -- After discount and tax

    -- Display order
    line_number INT DEFAULT 1,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE credit_note_items IS 'Line items for credit notes';

-- ============================================================================
-- 4. CREDIT NOTE APPLICATIONS TABLE - Track applications to invoices
-- ============================================================================

CREATE TABLE IF NOT EXISTS credit_note_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    credit_note_id UUID NOT NULL REFERENCES credit_notes(id),
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id),

    -- Application details
    amount_applied BIGINT NOT NULL,
    application_date DATE NOT NULL,

    -- Accounting integration
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    -- Prevent duplicate application to same invoice
    CONSTRAINT uq_cn_application UNIQUE(credit_note_id, invoice_id)
);

COMMENT ON TABLE credit_note_applications IS 'Tracks credit note applications to specific invoices';

-- ============================================================================
-- 5. CREDIT NOTE REFUNDS TABLE - Track cash refunds
-- ============================================================================

CREATE TABLE IF NOT EXISTS credit_note_refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    credit_note_id UUID NOT NULL REFERENCES credit_notes(id),

    -- Refund details
    amount BIGINT NOT NULL,
    refund_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,  -- cash, transfer, check
    account_id UUID NOT NULL,             -- Kas/Bank account from CoA
    bank_account_id UUID,
    reference VARCHAR(100),
    notes TEXT,

    -- Accounting
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL
);

COMMENT ON TABLE credit_note_refunds IS 'Tracks cash refunds issued from credit notes';

-- ============================================================================
-- 6. CREDIT NOTE SEQUENCE TABLE - Auto-numbering
-- ============================================================================

CREATE TABLE IF NOT EXISTS credit_note_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,       -- Format: YYYY-MM
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'CN',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_cn_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 7. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_cn_tenant_status ON credit_notes(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cn_tenant_date ON credit_notes(tenant_id, credit_note_date);
CREATE INDEX IF NOT EXISTS idx_cn_customer ON credit_notes(customer_id);
CREATE INDEX IF NOT EXISTS idx_cn_customer_name ON credit_notes(tenant_id, customer_name);
CREATE INDEX IF NOT EXISTS idx_cn_number ON credit_notes(tenant_id, credit_note_number);
CREATE INDEX IF NOT EXISTS idx_cn_original_invoice ON credit_notes(original_invoice_id);

CREATE INDEX IF NOT EXISTS idx_cn_items_cn ON credit_note_items(credit_note_id);

CREATE INDEX IF NOT EXISTS idx_cn_apps_cn ON credit_note_applications(credit_note_id);
CREATE INDEX IF NOT EXISTS idx_cn_apps_invoice ON credit_note_applications(invoice_id);
CREATE INDEX IF NOT EXISTS idx_cn_apps_tenant ON credit_note_applications(tenant_id);

CREATE INDEX IF NOT EXISTS idx_cn_refunds_cn ON credit_note_refunds(credit_note_id);
CREATE INDEX IF NOT EXISTS idx_cn_refunds_tenant ON credit_note_refunds(tenant_id);

-- ============================================================================
-- 8. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE credit_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_note_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_note_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_note_refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_note_sequences ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS rls_credit_notes ON credit_notes;
DROP POLICY IF EXISTS rls_credit_note_items ON credit_note_items;
DROP POLICY IF EXISTS rls_credit_note_applications ON credit_note_applications;
DROP POLICY IF EXISTS rls_credit_note_refunds ON credit_note_refunds;
DROP POLICY IF EXISTS rls_credit_note_sequences ON credit_note_sequences;

CREATE POLICY rls_credit_notes ON credit_notes
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_credit_note_items ON credit_note_items
    FOR ALL USING (credit_note_id IN (
        SELECT id FROM credit_notes WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_credit_note_applications ON credit_note_applications
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_credit_note_refunds ON credit_note_refunds
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_credit_note_sequences ON credit_note_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 9. FUNCTIONS
-- ============================================================================

-- Generate credit note number
CREATE OR REPLACE FUNCTION generate_credit_note_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'CN'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_cn_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO credit_note_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = credit_note_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: CN-YYMM-0001
    v_cn_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_cn_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_credit_note_number IS 'Generates sequential credit note number per tenant per month';

-- ============================================================================
-- 10. TRIGGERS FOR STATUS UPDATE
-- ============================================================================

-- Function to update credit note status based on applications/refunds
CREATE OR REPLACE FUNCTION update_credit_note_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_cn_id UUID;
BEGIN
    -- Determine credit_note_id based on operation
    IF TG_OP = 'DELETE' THEN
        v_cn_id := OLD.credit_note_id;
    ELSE
        v_cn_id := NEW.credit_note_id;
    END IF;

    -- Get credit note total
    SELECT total_amount, status INTO v_total_amount, v_new_status
    FROM credit_notes WHERE id = v_cn_id;

    -- Skip if draft or void
    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Sum applications
    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM credit_note_applications WHERE credit_note_id = v_cn_id;

    -- Sum refunds
    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM credit_note_refunds WHERE credit_note_id = v_cn_id;

    -- Determine new status
    IF (v_total_applied + v_total_refunded) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_refunded) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    -- Update credit note
    UPDATE credit_notes
    SET amount_applied = v_total_applied,
        amount_refunded = v_total_refunded,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_cn_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- Drop existing triggers if they exist
DROP TRIGGER IF EXISTS trg_update_cn_on_application ON credit_note_applications;
DROP TRIGGER IF EXISTS trg_update_cn_on_refund ON credit_note_refunds;

CREATE TRIGGER trg_update_cn_on_application
    AFTER INSERT OR UPDATE OR DELETE ON credit_note_applications
    FOR EACH ROW EXECUTE FUNCTION update_credit_note_status();

CREATE TRIGGER trg_update_cn_on_refund
    AFTER INSERT OR UPDATE OR DELETE ON credit_note_refunds
    FOR EACH ROW EXECUTE FUNCTION update_credit_note_status();

-- ============================================================================
-- 11. UPDATED_AT TRIGGER
-- ============================================================================

CREATE OR REPLACE FUNCTION update_credit_notes_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_credit_notes_updated_at ON credit_notes;
CREATE TRIGGER trg_credit_notes_updated_at
    BEFORE UPDATE ON credit_notes
    FOR EACH ROW EXECUTE FUNCTION update_credit_notes_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
