-- =============================================
-- V095: Payment Requests (Permintaan Pembayaran)
-- Purpose: Digitizing transfer request workflow
-- Replaces WA-based transfer requests with auditable system
--
-- Iron Laws Compliance:
-- - Law 6: Source Traceability - journal_entry_id untuk link ke journal
-- - Law 12: Audit Immutability - created_at, updated_at, actor tracking
-- =============================================

-- Status enum
CREATE TYPE payment_request_status AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED',
    'CANCELLED',
    'PAID',
    'POSTED'
);

-- Main table
CREATE TABLE payment_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    request_number VARCHAR(50) NOT NULL,
    requested_by UUID NOT NULL,
    requested_by_name VARCHAR(255),
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    purpose TEXT NOT NULL,
    description TEXT,
    amount BIGINT NOT NULL,
    reference_type VARCHAR(50),
    reference_id UUID,
    reference_number VARCHAR(100),
    bank_account_from UUID REFERENCES bank_accounts(id),
    bank_account_from_name VARCHAR(255),
    recipient_bank_name VARCHAR(100),
    recipient_account_number VARCHAR(50),
    recipient_account_name VARCHAR(255),
    status payment_request_status DEFAULT 'PENDING',
    approved_by UUID,
    approved_by_name VARCHAR(255),
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,
    paid_at TIMESTAMPTZ,
    paid_by UUID,
    paid_by_name VARCHAR(255),
    proof_url TEXT,
    proof_filename VARCHAR(255),
    payment_reference VARCHAR(100),
    posted_at TIMESTAMPTZ,
    journal_entry_id UUID REFERENCES journal_entries(id),
    confidentiality_level confidentiality_level DEFAULT 'L3',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, request_number)
);

-- Indexes
CREATE INDEX idx_payment_requests_tenant ON payment_requests(tenant_id);
CREATE INDEX idx_payment_requests_status ON payment_requests(tenant_id, status);
CREATE INDEX idx_payment_requests_requestor ON payment_requests(requested_by);
CREATE INDEX idx_payment_requests_fcl ON payment_requests(tenant_id, confidentiality_level);
CREATE INDEX idx_payment_requests_reference ON payment_requests(reference_type, reference_id);

-- RLS
ALTER TABLE payment_requests ENABLE ROW LEVEL SECURITY;

CREATE POLICY fcl_payment_requests ON payment_requests
    FOR ALL
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND (
            current_setting('app.user_visibility', true) IS NULL
            OR current_setting('app.user_visibility', true) = ''
            OR confidentiality_level::text = ANY(string_to_array(current_setting('app.user_visibility', true), ','))
        )
    );

-- Function to generate request number
CREATE OR REPLACE FUNCTION generate_payment_request_number(p_tenant_id TEXT)
RETURNS VARCHAR(50) AS $$
DECLARE
    v_year TEXT;
    v_seq INT;
    v_number VARCHAR(50);
BEGIN
    v_year := TO_CHAR(NOW(), 'YYYY');
    SELECT COALESCE(MAX(
        CAST(SUBSTRING(request_number FROM 'PR-' || v_year || '-(\d+)') AS INT)
    ), 0) + 1
    INTO v_seq
    FROM payment_requests
    WHERE tenant_id = p_tenant_id
    AND request_number LIKE 'PR-' || v_year || '-%';
    v_number := 'PR-' || v_year || '-' || LPAD(v_seq::TEXT, 4, '0');
    RETURN v_number;
END;
$$ LANGUAGE plpgsql;

-- Comments
COMMENT ON TABLE payment_requests IS 'Payment request workflow - digitizing transfer culture';
COMMENT ON COLUMN payment_requests.amount IS 'Amount in smallest currency unit';
COMMENT ON COLUMN payment_requests.reference_type IS 'Source document type: PURCHASE_INVOICE, EXPENSE, PAYROLL, OTHER';
