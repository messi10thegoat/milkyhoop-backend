-- ============================================================================
-- V088: Bill Payments V2 - Enhanced Payment Out System
-- ============================================================================
-- Purpose: Create a new bill_payments_v2 table with proper structure for
--          multi-bill allocation, similar to receive_payments for AR
-- ============================================================================

-- ============================================================================
-- 1. CREATE BILL PAYMENTS V2 TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS bill_payments_v2 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identity
    payment_number VARCHAR(50) NOT NULL,
    reference_number VARCHAR(100),

    -- Vendor
    vendor_id UUID REFERENCES vendors(id),
    vendor_name VARCHAR(255) NOT NULL,

    -- Payment Details
    payment_date DATE NOT NULL,
    payment_method VARCHAR(20) NOT NULL DEFAULT 'bank_transfer',
    bank_account_id UUID REFERENCES bank_accounts(id),
    bank_account_name VARCHAR(255),

    -- Multi-currency
    currency_code VARCHAR(3) DEFAULT 'IDR',
    exchange_rate DECIMAL(18, 6) DEFAULT 1.0,
    amount_in_base_currency BIGINT,

    -- Amounts (in IDR cents)
    total_amount BIGINT NOT NULL,
    allocated_amount BIGINT DEFAULT 0,
    unapplied_amount BIGINT DEFAULT 0,

    -- Discount & Fees
    discount_amount BIGINT DEFAULT 0,
    discount_account_id UUID,
    bank_fee_amount BIGINT DEFAULT 0,
    bank_fee_account_id UUID,

    -- Check/Giro Details
    check_number VARCHAR(50),
    check_due_date DATE,
    check_bank_name VARCHAR(100),

    -- Source (from deposit or new payment)
    source_type VARCHAR(20) DEFAULT 'cash',
    source_deposit_id UUID REFERENCES vendor_deposits(id),

    -- Status
    status VARCHAR(20) DEFAULT 'draft' CHECK (status IN ('draft', 'posted', 'voided')),

    -- Journal Integration
    journal_id UUID,
    journal_number VARCHAR(50),
    void_journal_id UUID,

    -- Overpayment creates deposit
    created_deposit_id UUID REFERENCES vendor_deposits(id),
    created_deposit_number VARCHAR(50),

    -- Bank transaction link
    bank_transaction_id UUID,

    -- Tags
    tags TEXT[] DEFAULT '{}',

    -- Notes
    notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,

    CONSTRAINT uq_bill_payment_number UNIQUE (tenant_id, payment_number)
);

-- ============================================================================
-- 2. CREATE BILL PAYMENT ALLOCATIONS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS bill_payment_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id UUID NOT NULL REFERENCES bill_payments_v2(id) ON DELETE CASCADE,
    bill_id UUID NOT NULL REFERENCES bills(id),

    -- Amounts
    remaining_before BIGINT NOT NULL,
    amount_applied BIGINT NOT NULL,
    remaining_after BIGINT NOT NULL,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 3. CREATE SEQUENCE TABLE FOR PAYMENT NUMBERS
-- ============================================================================

CREATE TABLE IF NOT EXISTS bill_payment_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    prefix VARCHAR(10) DEFAULT 'PAY',
    last_number INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_bill_pay_seq_tenant_month UNIQUE (tenant_id, year_month)
);

-- ============================================================================
-- 4. CREATE INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_bill_payments_v2_tenant ON bill_payments_v2(tenant_id);
CREATE INDEX IF NOT EXISTS idx_bill_payments_v2_status ON bill_payments_v2(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_bill_payments_v2_vendor ON bill_payments_v2(vendor_id);
CREATE INDEX IF NOT EXISTS idx_bill_payments_v2_date ON bill_payments_v2(tenant_id, payment_date DESC);
CREATE INDEX IF NOT EXISTS idx_bill_payments_v2_number ON bill_payments_v2(tenant_id, payment_number);
CREATE INDEX IF NOT EXISTS idx_bill_payments_v2_created ON bill_payments_v2(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bill_payment_allocations_payment ON bill_payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_bill_payment_allocations_bill ON bill_payment_allocations(bill_id);

-- ============================================================================
-- 5. ENABLE ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE bill_payments_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_payment_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_payment_sequences ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY rls_bill_payments_v2 ON bill_payments_v2
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bill_payment_allocations ON bill_payment_allocations
    FOR ALL USING (payment_id IN (
        SELECT id FROM bill_payments_v2 WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_bill_payment_sequences ON bill_payment_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. COMMENTS
-- ============================================================================

COMMENT ON TABLE bill_payments_v2 IS 'Pembayaran Keluar (Payment Out) - Vendor payments for purchase invoices';
COMMENT ON TABLE bill_payment_allocations IS 'Allocation of bill payments to individual bills';
COMMENT ON TABLE bill_payment_sequences IS 'Sequence numbers for bill payment numbering';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V088: Bill Payments V2 completed';
    RAISE NOTICE '- Created bill_payments_v2 table';
    RAISE NOTICE '- Created bill_payment_allocations table';
    RAISE NOTICE '- Created bill_payment_sequences table';
    RAISE NOTICE '- Created indexes and RLS policies';
END $$;
