-- ============================================================================
-- V036: Customer Deposits Module (Uang Muka Pelanggan)
-- ============================================================================
-- Purpose: Track customer advance payments (deposits) and their applications
-- Creates tables: customer_deposits, customer_deposit_applications,
--                 customer_deposit_refunds, customer_deposit_sequences
-- ============================================================================

-- ============================================================================
-- 1. ADD NEW ACCOUNT TO COA (2-10400 - Uang Muka Pelanggan)
-- ============================================================================

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '2-10400',
    'Uang Muka Pelanggan',
    'LIABILITY',
    'CREDIT',
    '2-10000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '2-10400' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 2. CUSTOMER DEPOSITS TABLE - Main records
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_deposits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Deposit identification
    deposit_number VARCHAR(50) NOT NULL,

    -- Customer reference
    customer_id VARCHAR(255),
    customer_name VARCHAR(255) NOT NULL,

    -- Amounts (BIGINT for IDR)
    amount BIGINT NOT NULL,
    amount_applied BIGINT DEFAULT 0,
    amount_refunded BIGINT DEFAULT 0,

    -- Status: draft -> posted -> partial/applied/void
    status VARCHAR(20) DEFAULT 'draft',

    -- Payment details
    deposit_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,
    account_id UUID NOT NULL,
    bank_account_id UUID,
    reference VARCHAR(100),

    -- Notes
    notes TEXT,

    -- Accounting integration
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

    CONSTRAINT uq_cust_deposits_tenant_number UNIQUE(tenant_id, deposit_number),
    CONSTRAINT chk_cust_deposit_status CHECK (status IN ('draft', 'posted', 'partial', 'applied', 'void')),
    CONSTRAINT chk_cust_deposit_method CHECK (payment_method IN ('cash', 'transfer', 'check', 'other'))
);

COMMENT ON TABLE customer_deposits IS 'Uang Muka Pelanggan - Customer Deposits/Advance Payments';
COMMENT ON COLUMN customer_deposits.status IS 'draft=not posted, posted=available, partial=partially applied, applied=fully used, void=cancelled';

-- ============================================================================
-- 3. CUSTOMER DEPOSIT APPLICATIONS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_deposit_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    deposit_id UUID NOT NULL REFERENCES customer_deposits(id),
    invoice_id UUID NOT NULL,
    invoice_number VARCHAR(50),

    -- Application details
    amount_applied BIGINT NOT NULL,
    application_date DATE NOT NULL,

    -- Accounting integration
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    -- Prevent duplicate application to same invoice
    CONSTRAINT uq_cust_deposit_application UNIQUE(deposit_id, invoice_id)
);

COMMENT ON TABLE customer_deposit_applications IS 'Tracks deposit applications to specific invoices';

-- ============================================================================
-- 4. CUSTOMER DEPOSIT REFUNDS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_deposit_refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    deposit_id UUID NOT NULL REFERENCES customer_deposits(id),

    -- Refund details
    amount BIGINT NOT NULL,
    refund_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,
    account_id UUID NOT NULL,
    bank_account_id UUID,
    reference VARCHAR(100),
    notes TEXT,

    -- Accounting
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL
);

COMMENT ON TABLE customer_deposit_refunds IS 'Tracks refunds issued from customer deposits';

-- ============================================================================
-- 5. CUSTOMER DEPOSIT SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_deposit_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'DEP',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_cust_dep_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 6. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_cust_dep_tenant_status ON customer_deposits(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cust_dep_tenant_date ON customer_deposits(tenant_id, deposit_date);
CREATE INDEX IF NOT EXISTS idx_cust_dep_customer ON customer_deposits(customer_id);
CREATE INDEX IF NOT EXISTS idx_cust_dep_customer_name ON customer_deposits(tenant_id, customer_name);
CREATE INDEX IF NOT EXISTS idx_cust_dep_number ON customer_deposits(tenant_id, deposit_number);

CREATE INDEX IF NOT EXISTS idx_cust_dep_apps_deposit ON customer_deposit_applications(deposit_id);
CREATE INDEX IF NOT EXISTS idx_cust_dep_apps_invoice ON customer_deposit_applications(invoice_id);
CREATE INDEX IF NOT EXISTS idx_cust_dep_apps_tenant ON customer_deposit_applications(tenant_id);

CREATE INDEX IF NOT EXISTS idx_cust_dep_refunds_deposit ON customer_deposit_refunds(deposit_id);
CREATE INDEX IF NOT EXISTS idx_cust_dep_refunds_tenant ON customer_deposit_refunds(tenant_id);

-- ============================================================================
-- 7. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE customer_deposits ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_deposit_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_deposit_refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_deposit_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_customer_deposits ON customer_deposits;
DROP POLICY IF EXISTS rls_customer_deposit_applications ON customer_deposit_applications;
DROP POLICY IF EXISTS rls_customer_deposit_refunds ON customer_deposit_refunds;
DROP POLICY IF EXISTS rls_customer_deposit_sequences ON customer_deposit_sequences;

CREATE POLICY rls_customer_deposits ON customer_deposits
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_customer_deposit_applications ON customer_deposit_applications
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_customer_deposit_refunds ON customer_deposit_refunds
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_customer_deposit_sequences ON customer_deposit_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 8. FUNCTIONS
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_customer_deposit_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'DEP'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_deposit_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO customer_deposit_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = customer_deposit_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: DEP-YYMM-0001
    v_deposit_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_deposit_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 9. TRIGGERS FOR STATUS UPDATE
-- ============================================================================

CREATE OR REPLACE FUNCTION update_customer_deposit_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_deposit_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_deposit_id := OLD.deposit_id;
    ELSE
        v_deposit_id := NEW.deposit_id;
    END IF;

    SELECT amount, status INTO v_total_amount, v_new_status
    FROM customer_deposits WHERE id = v_deposit_id;

    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM customer_deposit_applications WHERE deposit_id = v_deposit_id;

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM customer_deposit_refunds WHERE deposit_id = v_deposit_id;

    IF (v_total_applied + v_total_refunded) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_refunded) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    UPDATE customer_deposits
    SET amount_applied = v_total_applied,
        amount_refunded = v_total_refunded,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_deposit_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_deposit_on_application ON customer_deposit_applications;
DROP TRIGGER IF EXISTS trg_update_deposit_on_refund ON customer_deposit_refunds;

CREATE TRIGGER trg_update_deposit_on_application
    AFTER INSERT OR UPDATE OR DELETE ON customer_deposit_applications
    FOR EACH ROW EXECUTE FUNCTION update_customer_deposit_status();

CREATE TRIGGER trg_update_deposit_on_refund
    AFTER INSERT OR UPDATE OR DELETE ON customer_deposit_refunds
    FOR EACH ROW EXECUTE FUNCTION update_customer_deposit_status();

-- ============================================================================
-- 10. UPDATED_AT TRIGGER
-- ============================================================================

CREATE OR REPLACE FUNCTION update_customer_deposits_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_customer_deposits_updated_at ON customer_deposits;
CREATE TRIGGER trg_customer_deposits_updated_at
    BEFORE UPDATE ON customer_deposits
    FOR EACH ROW EXECUTE FUNCTION update_customer_deposits_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V036: Customer Deposits created successfully';
    RAISE NOTICE 'Tables: customer_deposits, customer_deposit_applications, customer_deposit_refunds';
    RAISE NOTICE 'New account: 2-10400 Uang Muka Pelanggan';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
