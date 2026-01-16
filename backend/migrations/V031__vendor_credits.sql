-- ============================================================================
-- V031: Vendor Credits Module (Kredit Vendor / Purchase Returns)
-- ============================================================================
-- Purpose: Vendor credits for handling purchase returns and AP adjustments
-- Creates tables: vendor_credits, vendor_credit_items, vendor_credit_applications,
--                 vendor_credit_refunds, vendor_credit_sequences
-- ============================================================================

-- ============================================================================
-- 1. VERIFY/CREATE REQUIRED ACCOUNTS
-- ============================================================================

-- Add Purchase Returns account (5-10300) if not exists
INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active, is_system)
SELECT
    t.tenant_id::uuid,
    '5-10300',
    'Retur Pembelian',
    'EXPENSE',
    'CREDIT',  -- Contra expense account
    (SELECT id FROM chart_of_accounts c2 WHERE c2.tenant_id = t.tenant_id::uuid AND c2.code = '5-00000' LIMIT 1),
    true,
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE code = '5-10300' AND tenant_id = t.tenant_id::uuid
);

-- ============================================================================
-- 2. VENDOR CREDITS TABLE - Main records
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Vendor credit identification
    vendor_credit_number VARCHAR(50) NOT NULL,

    -- Vendor reference
    vendor_id UUID REFERENCES vendors(id),
    vendor_name VARCHAR(255) NOT NULL,

    -- Original bill reference (optional)
    original_bill_id UUID REFERENCES bills(id),
    original_bill_number VARCHAR(50),

    -- Amounts (BIGINT for IDR, stored in smallest unit)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,
    amount_applied BIGINT DEFAULT 0,    -- Amount applied to bills
    amount_refunded BIGINT DEFAULT 0,   -- Amount refunded by vendor

    -- Status workflow: draft -> posted -> partial/applied/void
    status VARCHAR(20) DEFAULT 'draft',

    -- Dates
    vendor_credit_date DATE NOT NULL,

    -- Reason for vendor credit
    reason VARCHAR(30) NOT NULL,  -- return, pricing_error, discount, damaged, other
    reason_detail TEXT,

    -- Reference
    ref_no VARCHAR(100),          -- Vendor's credit note number
    notes TEXT,

    -- Accounting integration
    ap_id UUID,                          -- Link to accounts_payable
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

    CONSTRAINT uq_vendor_credits_tenant_number UNIQUE(tenant_id, vendor_credit_number),
    CONSTRAINT chk_vendor_credit_status CHECK (status IN ('draft', 'posted', 'partial', 'applied', 'void')),
    CONSTRAINT chk_vendor_credit_reason CHECK (reason IN ('return', 'pricing_error', 'discount', 'damaged', 'other'))
);

COMMENT ON TABLE vendor_credits IS 'Kredit Vendor - Vendor Credits for purchase returns and AP adjustments';
COMMENT ON COLUMN vendor_credits.status IS 'draft=not posted, posted=available for use, partial=partially applied, applied=fully used, void=cancelled';
COMMENT ON COLUMN vendor_credits.ref_no IS 'Vendor credit note number from supplier';

-- ============================================================================
-- 3. VENDOR CREDIT ITEMS TABLE - Line items
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_credit_id UUID NOT NULL REFERENCES vendor_credits(id) ON DELETE CASCADE,

    -- Product reference
    item_id UUID,
    item_code VARCHAR(50),
    description VARCHAR(500) NOT NULL,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(20),
    unit_price BIGINT NOT NULL,

    -- Original bill item reference
    original_bill_item_id UUID,

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

    -- Pharmacy-specific fields
    batch_no VARCHAR(100),
    exp_date DATE,

    -- Display order
    line_number INT DEFAULT 1,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE vendor_credit_items IS 'Line items for vendor credits';

-- ============================================================================
-- 4. VENDOR CREDIT APPLICATIONS TABLE - Track applications to bills
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    vendor_credit_id UUID NOT NULL REFERENCES vendor_credits(id),
    bill_id UUID NOT NULL REFERENCES bills(id),

    -- Application details
    amount_applied BIGINT NOT NULL,
    application_date DATE NOT NULL,

    -- Accounting integration
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    -- Prevent duplicate application to same bill
    CONSTRAINT uq_vc_application UNIQUE(vendor_credit_id, bill_id)
);

COMMENT ON TABLE vendor_credit_applications IS 'Tracks vendor credit applications to specific bills';

-- ============================================================================
-- 5. VENDOR CREDIT REFUNDS TABLE - Track cash received from vendor
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    vendor_credit_id UUID NOT NULL REFERENCES vendor_credits(id),

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

COMMENT ON TABLE vendor_credit_refunds IS 'Tracks cash refunds received from vendors';

-- ============================================================================
-- 6. VENDOR CREDIT SEQUENCE TABLE - Auto-numbering
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,       -- Format: YYYY-MM
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'VC',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_vc_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 7. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_vc_tenant_status ON vendor_credits(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_vc_tenant_date ON vendor_credits(tenant_id, vendor_credit_date);
CREATE INDEX IF NOT EXISTS idx_vc_vendor ON vendor_credits(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vc_vendor_name ON vendor_credits(tenant_id, vendor_name);
CREATE INDEX IF NOT EXISTS idx_vc_number ON vendor_credits(tenant_id, vendor_credit_number);
CREATE INDEX IF NOT EXISTS idx_vc_original_bill ON vendor_credits(original_bill_id);

CREATE INDEX IF NOT EXISTS idx_vc_items_vc ON vendor_credit_items(vendor_credit_id);

CREATE INDEX IF NOT EXISTS idx_vc_apps_vc ON vendor_credit_applications(vendor_credit_id);
CREATE INDEX IF NOT EXISTS idx_vc_apps_bill ON vendor_credit_applications(bill_id);
CREATE INDEX IF NOT EXISTS idx_vc_apps_tenant ON vendor_credit_applications(tenant_id);

CREATE INDEX IF NOT EXISTS idx_vc_refunds_vc ON vendor_credit_refunds(vendor_credit_id);
CREATE INDEX IF NOT EXISTS idx_vc_refunds_tenant ON vendor_credit_refunds(tenant_id);

-- ============================================================================
-- 8. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE vendor_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_credit_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_credit_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_credit_refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_credit_sequences ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS rls_vendor_credits ON vendor_credits;
DROP POLICY IF EXISTS rls_vendor_credit_items ON vendor_credit_items;
DROP POLICY IF EXISTS rls_vendor_credit_applications ON vendor_credit_applications;
DROP POLICY IF EXISTS rls_vendor_credit_refunds ON vendor_credit_refunds;
DROP POLICY IF EXISTS rls_vendor_credit_sequences ON vendor_credit_sequences;

CREATE POLICY rls_vendor_credits ON vendor_credits
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_vendor_credit_items ON vendor_credit_items
    FOR ALL USING (vendor_credit_id IN (
        SELECT id FROM vendor_credits WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_vendor_credit_applications ON vendor_credit_applications
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_vendor_credit_refunds ON vendor_credit_refunds
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_vendor_credit_sequences ON vendor_credit_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 9. FUNCTIONS
-- ============================================================================

-- Generate vendor credit number
CREATE OR REPLACE FUNCTION generate_vendor_credit_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'VC'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_vc_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO vendor_credit_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = vendor_credit_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: VC-YYMM-0001
    v_vc_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_vc_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_vendor_credit_number IS 'Generates sequential vendor credit number per tenant per month';

-- ============================================================================
-- 10. TRIGGERS FOR STATUS UPDATE
-- ============================================================================

-- Function to update vendor credit status based on applications/refunds
CREATE OR REPLACE FUNCTION update_vendor_credit_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_vc_id UUID;
BEGIN
    -- Determine vendor_credit_id based on operation
    IF TG_OP = 'DELETE' THEN
        v_vc_id := OLD.vendor_credit_id;
    ELSE
        v_vc_id := NEW.vendor_credit_id;
    END IF;

    -- Get vendor credit total
    SELECT total_amount, status INTO v_total_amount, v_new_status
    FROM vendor_credits WHERE id = v_vc_id;

    -- Skip if draft or void
    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Sum applications
    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM vendor_credit_applications WHERE vendor_credit_id = v_vc_id;

    -- Sum refunds
    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM vendor_credit_refunds WHERE vendor_credit_id = v_vc_id;

    -- Determine new status
    IF (v_total_applied + v_total_refunded) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_refunded) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    -- Update vendor credit
    UPDATE vendor_credits
    SET amount_applied = v_total_applied,
        amount_refunded = v_total_refunded,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_vc_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- Drop existing triggers if they exist
DROP TRIGGER IF EXISTS trg_update_vc_on_application ON vendor_credit_applications;
DROP TRIGGER IF EXISTS trg_update_vc_on_refund ON vendor_credit_refunds;

CREATE TRIGGER trg_update_vc_on_application
    AFTER INSERT OR UPDATE OR DELETE ON vendor_credit_applications
    FOR EACH ROW EXECUTE FUNCTION update_vendor_credit_status();

CREATE TRIGGER trg_update_vc_on_refund
    AFTER INSERT OR UPDATE OR DELETE ON vendor_credit_refunds
    FOR EACH ROW EXECUTE FUNCTION update_vendor_credit_status();

-- ============================================================================
-- 11. UPDATED_AT TRIGGER
-- ============================================================================

CREATE OR REPLACE FUNCTION update_vendor_credits_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vendor_credits_updated_at ON vendor_credits;
CREATE TRIGGER trg_vendor_credits_updated_at
    BEFORE UPDATE ON vendor_credits
    FOR EACH ROW EXECUTE FUNCTION update_vendor_credits_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
