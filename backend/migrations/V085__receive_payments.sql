-- ============================================================================
-- V085: Receive Payments Module (Penerimaan Pembayaran)
-- ============================================================================
-- Purpose: Track customer payments for invoices with deposit integration
-- Creates tables: receive_payments, receive_payment_allocations, receive_payment_sequences
-- Integrates with: customer_deposits (overpayment creates deposit, can pay from deposit)
-- ============================================================================

-- ============================================================================
-- 1. RECEIVE PAYMENTS TABLE - Main records
-- ============================================================================

CREATE TABLE IF NOT EXISTS receive_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Payment identification
    payment_number VARCHAR(50) NOT NULL,

    -- Customer reference
    customer_id UUID REFERENCES customers(id),
    customer_name VARCHAR(255) NOT NULL,

    -- Payment details
    payment_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,  -- cash, bank_transfer
    bank_account_id UUID NOT NULL,        -- CoA account for cash/bank
    bank_account_name VARCHAR(255) NOT NULL,

    -- Source tracking (cash payment vs from deposit)
    source_type VARCHAR(20) NOT NULL DEFAULT 'cash',  -- 'cash', 'deposit'
    source_deposit_id UUID REFERENCES customer_deposits(id),

    -- Amounts (BIGINT for IDR)
    total_amount BIGINT NOT NULL,
    allocated_amount BIGINT NOT NULL DEFAULT 0,
    unapplied_amount BIGINT NOT NULL DEFAULT 0,
    discount_amount BIGINT NOT NULL DEFAULT 0,
    discount_account_id UUID,  -- CoA account for discount

    -- Status: draft -> posted -> voided
    status VARCHAR(20) DEFAULT 'draft',

    -- Reference & notes
    reference_number VARCHAR(100),
    notes TEXT,

    -- Accounting integration
    journal_id UUID,
    journal_number VARCHAR(50),
    void_journal_id UUID,

    -- Overpayment creates deposit - track link
    created_deposit_id UUID REFERENCES customer_deposits(id),

    -- Status tracking
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_rcv_payments_tenant_number UNIQUE(tenant_id, payment_number),
    CONSTRAINT chk_rcv_payment_status CHECK (status IN ('draft', 'posted', 'voided')),
    CONSTRAINT chk_rcv_payment_method CHECK (payment_method IN ('cash', 'bank_transfer')),
    CONSTRAINT chk_rcv_payment_source CHECK (source_type IN ('cash', 'deposit')),
    CONSTRAINT chk_rcv_payment_source_valid CHECK (
        (source_type = 'cash' AND source_deposit_id IS NULL) OR
        (source_type = 'deposit' AND source_deposit_id IS NOT NULL)
    )
);

COMMENT ON TABLE receive_payments IS 'Penerimaan Pembayaran - Customer payments for invoices';
COMMENT ON COLUMN receive_payments.source_type IS 'cash=normal payment, deposit=payment from customer deposit';
COMMENT ON COLUMN receive_payments.created_deposit_id IS 'If overpayment, links to auto-created customer deposit';

-- ============================================================================
-- 2. RECEIVE PAYMENT ALLOCATIONS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS receive_payment_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payment_id UUID NOT NULL REFERENCES receive_payments(id) ON DELETE CASCADE,

    -- Invoice reference
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id),
    invoice_number VARCHAR(50) NOT NULL,

    -- Allocation details
    invoice_amount BIGINT NOT NULL,      -- Original invoice total
    remaining_before BIGINT NOT NULL,    -- Remaining before this payment
    amount_applied BIGINT NOT NULL,      -- Amount applied from this payment
    remaining_after BIGINT NOT NULL,     -- Remaining after this payment

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_rcv_payment_allocation UNIQUE(tenant_id, payment_id, invoice_id)
);

COMMENT ON TABLE receive_payment_allocations IS 'Tracks which invoices a payment is applied to';

-- ============================================================================
-- 3. RECEIVE PAYMENT SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS receive_payment_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INT NOT NULL DEFAULT 0,
    year INT NOT NULL
);

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_rcv_payments_tenant_status ON receive_payments(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_tenant_date ON receive_payments(tenant_id, payment_date);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_customer ON receive_payments(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_number ON receive_payments(tenant_id, payment_number);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_source_deposit ON receive_payments(source_deposit_id) WHERE source_deposit_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rcv_alloc_payment ON receive_payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_rcv_alloc_invoice ON receive_payment_allocations(invoice_id);
CREATE INDEX IF NOT EXISTS idx_rcv_alloc_tenant ON receive_payment_allocations(tenant_id);

-- ============================================================================
-- 5. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE receive_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE receive_payment_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE receive_payment_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_receive_payments ON receive_payments;
DROP POLICY IF EXISTS rls_receive_payment_allocations ON receive_payment_allocations;
DROP POLICY IF EXISTS rls_receive_payment_sequences ON receive_payment_sequences;

CREATE POLICY rls_receive_payments ON receive_payments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_receive_payment_allocations ON receive_payment_allocations
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_receive_payment_sequences ON receive_payment_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_receive_payment_number(p_tenant_id TEXT)
RETURNS VARCHAR AS $$
DECLARE
    v_year INT;
    v_next_number INT;
    v_payment_number VARCHAR(50);
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    INSERT INTO receive_payment_sequences (tenant_id, last_number, year)
    VALUES (p_tenant_id, 1, v_year)
    ON CONFLICT (tenant_id)
    DO UPDATE SET
        last_number = CASE
            WHEN receive_payment_sequences.year = v_year
            THEN receive_payment_sequences.last_number + 1
            ELSE 1
        END,
        year = v_year
    RETURNING last_number INTO v_next_number;

    -- Format: RCV-YYYY-NNNN
    v_payment_number := 'RCV-' || v_year || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_payment_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 7. UPDATED_AT TRIGGER
-- ============================================================================

CREATE OR REPLACE FUNCTION update_receive_payments_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_receive_payments_updated_at ON receive_payments;
CREATE TRIGGER trg_receive_payments_updated_at
    BEFORE UPDATE ON receive_payments
    FOR EACH ROW EXECUTE FUNCTION update_receive_payments_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V085: Receive Payments created successfully';
    RAISE NOTICE 'Tables: receive_payments, receive_payment_allocations, receive_payment_sequences';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
