-- ============================================================================
-- V100: Dual Status Model for Bill Payments
-- ============================================================================

ALTER TABLE bill_payments 
ADD COLUMN IF NOT EXISTS operational_status VARCHAR(20) DEFAULT 'CREATED',
ADD COLUMN IF NOT EXISTS accounting_status VARCHAR(20) DEFAULT 'UNPOSTED';

ALTER TABLE bill_payments
ADD CONSTRAINT chk_bp_operational_status CHECK (
    operational_status IN ('CREATED', 'PENDING_APPROVAL', 'APPROVED', 'SENT_TO_BANK', 'PROCESSING', 'CONFIRMED', 'FAILED', 'CANCELLED')
);

ALTER TABLE bill_payments
ADD CONSTRAINT chk_bp_accounting_status CHECK (
    accounting_status IN ('UNPOSTED', 'POSTED', 'REVERSED')
);

CREATE INDEX IF NOT EXISTS idx_bp_op_status ON bill_payments(tenant_id, operational_status);
CREATE INDEX IF NOT EXISTS idx_bp_acc_status ON bill_payments(tenant_id, accounting_status);
