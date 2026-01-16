-- ============================================================================
-- V031: Vendor Credits Module (Kredit Vendor / Purchase Returns) - FIXED
-- ============================================================================
-- Purpose: Vendor credits for handling purchase returns and AP adjustments
-- Adapted to actual database schema
-- ============================================================================

-- ============================================================================
-- 1. VERIFY/CREATE REQUIRED ACCOUNTS
-- ============================================================================

-- Add Purchase Returns account (5-10300) if not exists
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '5-10300',
    'Retur Pembelian',
    'EXPENSE',
    'CREDIT',  -- Contra expense account
    '5-00000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '5-10300' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 2. VENDOR CREDITS TABLE - Main records
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Vendor credit identification
    credit_number VARCHAR(50) NOT NULL,

    -- Vendor reference
    vendor_id UUID,
    vendor_name VARCHAR(255) NOT NULL,

    -- Original bill reference
    original_bill_id UUID,
    original_bill_number VARCHAR(50),

    -- Amounts (BIGINT for IDR)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,
    amount_applied BIGINT DEFAULT 0,
    amount_received BIGINT DEFAULT 0,   -- Cash received from vendor

    -- Status: draft -> posted -> partial/applied/void
    status VARCHAR(20) DEFAULT 'draft',

    -- Dates
    credit_date DATE NOT NULL,

    -- Reason
    reason VARCHAR(30) NOT NULL,
    reason_detail TEXT,

    -- Reference
    ref_no VARCHAR(100),
    notes TEXT,

    -- Accounting
    ap_id UUID,
    journal_id UUID,

    -- Status tracking
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    voided_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_vendor_credits_tenant_number UNIQUE(tenant_id, credit_number),
    CONSTRAINT chk_vc_status CHECK (status IN ('draft', 'posted', 'partial', 'applied', 'void')),
    CONSTRAINT chk_vc_reason CHECK (reason IN ('return', 'pricing_error', 'discount', 'damaged', 'other'))
);

COMMENT ON TABLE vendor_credits IS 'Kredit Vendor - Vendor Credits for purchase returns and AP adjustments';

-- ============================================================================
-- 3. VENDOR CREDIT ITEMS TABLE
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

    line_number INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 4. VENDOR CREDIT APPLICATIONS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    vendor_credit_id UUID NOT NULL REFERENCES vendor_credits(id),
    bill_id UUID,
    bill_number VARCHAR(50),

    amount_applied BIGINT NOT NULL,
    application_date DATE NOT NULL,

    journal_id UUID,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL
);

-- ============================================================================
-- 5. VENDOR CREDIT RECEIVED REFUNDS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    vendor_credit_id UUID NOT NULL REFERENCES vendor_credits(id),

    amount BIGINT NOT NULL,
    refund_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,
    account_id UUID NOT NULL,
    bank_account_id UUID,
    reference VARCHAR(100),
    notes TEXT,

    journal_id UUID,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL
);

-- ============================================================================
-- 6. VENDOR CREDIT SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendor_credit_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_vc_tenant_date ON vendor_credits(tenant_id, credit_date);
CREATE INDEX IF NOT EXISTS idx_vc_vendor ON vendor_credits(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vc_vendor_name ON vendor_credits(tenant_id, vendor_name);
CREATE INDEX IF NOT EXISTS idx_vc_number ON vendor_credits(tenant_id, credit_number);

CREATE INDEX IF NOT EXISTS idx_vc_items_vc ON vendor_credit_items(vendor_credit_id);

CREATE INDEX IF NOT EXISTS idx_vc_apps_vc ON vendor_credit_applications(vendor_credit_id);
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

    v_vc_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_vc_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 10. TRIGGERS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_vendor_credit_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_applied BIGINT;
    v_total_received BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_vc_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_vc_id := OLD.vendor_credit_id;
    ELSE
        v_vc_id := NEW.vendor_credit_id;
    END IF;

    SELECT total_amount, status INTO v_total_amount, v_new_status
    FROM vendor_credits WHERE id = v_vc_id;

    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM vendor_credit_applications WHERE vendor_credit_id = v_vc_id;

    SELECT COALESCE(SUM(amount), 0) INTO v_total_received
    FROM vendor_credit_refunds WHERE vendor_credit_id = v_vc_id;

    IF (v_total_applied + v_total_received) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_received) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    UPDATE vendor_credits
    SET amount_applied = v_total_applied,
        amount_received = v_total_received,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_vc_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_vc_on_application ON vendor_credit_applications;
DROP TRIGGER IF EXISTS trg_update_vc_on_refund ON vendor_credit_refunds;

CREATE TRIGGER trg_update_vc_on_application
    AFTER INSERT OR UPDATE OR DELETE ON vendor_credit_applications
    FOR EACH ROW EXECUTE FUNCTION update_vendor_credit_status();

CREATE TRIGGER trg_update_vc_on_refund
    AFTER INSERT OR UPDATE OR DELETE ON vendor_credit_refunds
    FOR EACH ROW EXECUTE FUNCTION update_vendor_credit_status();

-- updated_at trigger
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
