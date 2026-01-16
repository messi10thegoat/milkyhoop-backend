-- =============================================
-- V054: Vendor Deposits (Uang Muka Vendor)
-- Purpose: Advance payments to suppliers before receiving goods
-- =============================================

-- Vendor deposits master
CREATE TABLE vendor_deposits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Reference
    deposit_number VARCHAR(50) NOT NULL,
    deposit_date DATE NOT NULL,

    -- Vendor
    vendor_id UUID NOT NULL REFERENCES vendors(id),

    -- Amount
    amount BIGINT NOT NULL,
    applied_amount BIGINT DEFAULT 0,
    remaining_amount BIGINT GENERATED ALWAYS AS (amount - applied_amount) STORED,

    -- Payment info
    payment_method VARCHAR(50) DEFAULT 'transfer',
    bank_account_id UUID REFERENCES bank_accounts(id),
    reference VARCHAR(100),

    -- Related PO (optional)
    purchase_order_id UUID REFERENCES purchase_orders(id),

    -- Journal
    journal_id UUID REFERENCES journal_entries(id),

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, posted, partial, applied, void

    -- Notes
    notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_vendor_deposits_number UNIQUE(tenant_id, deposit_number),
    CONSTRAINT chk_vendor_deposits_applied CHECK (applied_amount >= 0 AND applied_amount <= amount)
);

-- Vendor deposit applications (to bills)
CREATE TABLE vendor_deposit_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_deposit_id UUID NOT NULL REFERENCES vendor_deposits(id),
    bill_id UUID NOT NULL REFERENCES bills(id),

    amount BIGINT NOT NULL,
    applied_date DATE NOT NULL,

    journal_id UUID REFERENCES journal_entries(id),

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT chk_vda_amount CHECK (amount > 0)
);

-- Vendor deposit refunds (if deposit not used)
CREATE TABLE vendor_deposit_refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_deposit_id UUID NOT NULL REFERENCES vendor_deposits(id),

    refund_date DATE NOT NULL,
    amount BIGINT NOT NULL,

    bank_account_id UUID REFERENCES bank_accounts(id),
    reference VARCHAR(100),

    journal_id UUID REFERENCES journal_entries(id),

    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT chk_vdr_amount CHECK (amount > 0)
);

-- Sequence
CREATE TABLE vendor_deposit_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0
);

-- RLS
ALTER TABLE vendor_deposits ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_deposit_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_deposit_refunds ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_vendor_deposits ON vendor_deposits
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_vendor_deposit_applications ON vendor_deposit_applications
    USING (vendor_deposit_id IN (SELECT id FROM vendor_deposits WHERE tenant_id = current_setting('app.tenant_id', true)));
CREATE POLICY rls_vendor_deposit_refunds ON vendor_deposit_refunds
    USING (vendor_deposit_id IN (SELECT id FROM vendor_deposits WHERE tenant_id = current_setting('app.tenant_id', true)));

-- Indexes
CREATE INDEX idx_vendor_deposits_tenant ON vendor_deposits(tenant_id);
CREATE INDEX idx_vendor_deposits_vendor ON vendor_deposits(vendor_id);
CREATE INDEX idx_vendor_deposits_status ON vendor_deposits(tenant_id, status);
CREATE INDEX idx_vendor_deposits_date ON vendor_deposits(tenant_id, deposit_date);
CREATE INDEX idx_vendor_deposit_applications_deposit ON vendor_deposit_applications(vendor_deposit_id);
CREATE INDEX idx_vendor_deposit_applications_bill ON vendor_deposit_applications(bill_id);

-- =============================================
-- Seed Account: 1-10800 Uang Muka Vendor
-- =============================================

-- Function to seed vendor deposit account for tenant
CREATE OR REPLACE FUNCTION seed_vendor_deposit_account(p_tenant_id TEXT)
RETURNS VOID AS $$
BEGIN
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, is_system, is_active
    ) VALUES (
        p_tenant_id, '1-10800', 'Uang Muka Vendor', 'ASSET', true, true
    ) ON CONFLICT (tenant_id, account_code) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- =============================================
-- Helper Functions
-- =============================================

-- Generate vendor deposit number
CREATE OR REPLACE FUNCTION generate_vendor_deposit_number(p_tenant_id TEXT)
RETURNS VARCHAR(50) AS $$
DECLARE
    v_number INTEGER;
    v_year TEXT;
BEGIN
    v_year := TO_CHAR(CURRENT_DATE, 'YYYY');

    INSERT INTO vendor_deposit_sequences (tenant_id, last_number)
    VALUES (p_tenant_id, 1)
    ON CONFLICT (tenant_id)
    DO UPDATE SET last_number = vendor_deposit_sequences.last_number + 1
    RETURNING last_number INTO v_number;

    RETURN 'VD-' || v_year || '-' || LPAD(v_number::TEXT, 5, '0');
END;
$$ LANGUAGE plpgsql;

-- Get vendor deposits for vendor
CREATE OR REPLACE FUNCTION get_vendor_deposits(
    p_vendor_id UUID,
    p_status VARCHAR(20) DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    deposit_number VARCHAR(50),
    deposit_date DATE,
    amount BIGINT,
    applied_amount BIGINT,
    remaining_amount BIGINT,
    status VARCHAR(20),
    reference VARCHAR(100),
    purchase_order_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        vd.id,
        vd.deposit_number,
        vd.deposit_date,
        vd.amount,
        vd.applied_amount,
        vd.remaining_amount,
        vd.status,
        vd.reference,
        vd.purchase_order_id
    FROM vendor_deposits vd
    WHERE vd.vendor_id = p_vendor_id
    AND (p_status IS NULL OR vd.status = p_status)
    ORDER BY vd.deposit_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get available deposits for application
CREATE OR REPLACE FUNCTION get_available_vendor_deposits(p_vendor_id UUID)
RETURNS TABLE (
    id UUID,
    deposit_number VARCHAR(50),
    deposit_date DATE,
    amount BIGINT,
    applied_amount BIGINT,
    remaining_amount BIGINT,
    reference VARCHAR(100)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        vd.id,
        vd.deposit_number,
        vd.deposit_date,
        vd.amount,
        vd.applied_amount,
        vd.remaining_amount,
        vd.reference
    FROM vendor_deposits vd
    WHERE vd.vendor_id = p_vendor_id
    AND vd.status IN ('posted', 'partial')
    AND vd.remaining_amount > 0
    ORDER BY vd.deposit_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get applications for a deposit
CREATE OR REPLACE FUNCTION get_vendor_deposit_applications(p_deposit_id UUID)
RETURNS TABLE (
    id UUID,
    bill_id UUID,
    bill_number VARCHAR(50),
    bill_date DATE,
    applied_amount BIGINT,
    applied_date DATE
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        vda.id,
        vda.bill_id,
        b.bill_number,
        b.bill_date,
        vda.amount as applied_amount,
        vda.applied_date
    FROM vendor_deposit_applications vda
    JOIN bills b ON vda.bill_id = b.id
    WHERE vda.vendor_deposit_id = p_deposit_id
    ORDER BY vda.applied_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get refunds for a deposit
CREATE OR REPLACE FUNCTION get_vendor_deposit_refunds(p_deposit_id UUID)
RETURNS TABLE (
    id UUID,
    refund_date DATE,
    amount BIGINT,
    reference VARCHAR(100),
    notes TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        vdr.id,
        vdr.refund_date,
        vdr.amount,
        vdr.reference,
        vdr.notes
    FROM vendor_deposit_refunds vdr
    WHERE vdr.vendor_deposit_id = p_deposit_id
    ORDER BY vdr.refund_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Update deposit status based on applied amount
CREATE OR REPLACE FUNCTION update_vendor_deposit_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_deposit RECORD;
BEGIN
    -- Get deposit
    SELECT * INTO v_deposit FROM vendor_deposits WHERE id = COALESCE(NEW.vendor_deposit_id, OLD.vendor_deposit_id);

    IF v_deposit.id IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Calculate totals
    SELECT COALESCE(SUM(amount), 0) INTO v_total_applied
    FROM vendor_deposit_applications WHERE vendor_deposit_id = v_deposit.id;

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM vendor_deposit_refunds WHERE vendor_deposit_id = v_deposit.id;

    -- Update applied amount
    UPDATE vendor_deposits SET
        applied_amount = v_total_applied + v_total_refunded,
        status = CASE
            WHEN v_total_applied + v_total_refunded >= amount THEN 'applied'
            WHEN v_total_applied + v_total_refunded > 0 THEN 'partial'
            ELSE status
        END,
        updated_at = NOW()
    WHERE id = v_deposit.id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- Trigger for applications
CREATE TRIGGER trg_vendor_deposit_application_status
    AFTER INSERT OR UPDATE OR DELETE ON vendor_deposit_applications
    FOR EACH ROW
    EXECUTE FUNCTION update_vendor_deposit_status();

-- Trigger for refunds
CREATE TRIGGER trg_vendor_deposit_refund_status
    AFTER INSERT OR UPDATE OR DELETE ON vendor_deposit_refunds
    FOR EACH ROW
    EXECUTE FUNCTION update_vendor_deposit_status();

-- Get vendor deposit summary
CREATE OR REPLACE FUNCTION get_vendor_deposit_summary(p_tenant_id TEXT)
RETURNS TABLE (
    total_deposits BIGINT,
    total_applied BIGINT,
    total_remaining BIGINT,
    deposit_count INTEGER,
    pending_count INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(amount), 0)::BIGINT as total_deposits,
        COALESCE(SUM(applied_amount), 0)::BIGINT as total_applied,
        COALESCE(SUM(remaining_amount), 0)::BIGINT as total_remaining,
        COUNT(*)::INTEGER as deposit_count,
        COUNT(CASE WHEN status IN ('posted', 'partial') AND remaining_amount > 0 THEN 1 END)::INTEGER as pending_count
    FROM vendor_deposits
    WHERE tenant_id = p_tenant_id
    AND status != 'void';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- Comments
-- =============================================
COMMENT ON TABLE vendor_deposits IS 'Advance payments to vendors before receiving goods/services';
COMMENT ON TABLE vendor_deposit_applications IS 'Application of deposits to bills (reduces AP)';
COMMENT ON TABLE vendor_deposit_refunds IS 'Refunds received when deposits are not used';
COMMENT ON COLUMN vendor_deposits.remaining_amount IS 'Auto-computed: amount - applied_amount';

/*
JOURNAL ENTRIES:

1. On POST (Pay Deposit):
   Dr. Uang Muka Vendor (1-10800)    amount
       Cr. Kas/Bank                      amount

2. On APPLY (to Bill):
   Dr. Hutang Usaha (2-10100)        applied_amount
       Cr. Uang Muka Vendor (1-10800)    applied_amount

3. On REFUND:
   Dr. Kas/Bank                      refund_amount
       Cr. Uang Muka Vendor (1-10800)    refund_amount
*/
