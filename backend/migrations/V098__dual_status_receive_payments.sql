-- ============================================================================
-- V098: Dual Status Model for Receive Payments
-- ============================================================================

ALTER TABLE receive_payments 
ADD COLUMN IF NOT EXISTS operational_status VARCHAR(20) DEFAULT 'CREATED',
ADD COLUMN IF NOT EXISTS accounting_status VARCHAR(20) DEFAULT 'UNPOSTED';

ALTER TABLE receive_payments
ADD CONSTRAINT chk_rcv_operational_status CHECK (
    operational_status IN ('CREATED', 'PENDING_APPROVAL', 'APPROVED', 'SENT_TO_BANK', 'PROCESSING', 'CONFIRMED', 'FAILED', 'CANCELLED')
);

ALTER TABLE receive_payments
ADD CONSTRAINT chk_rcv_accounting_status CHECK (
    accounting_status IN ('UNPOSTED', 'POSTED', 'REVERSED')
);

CREATE INDEX IF NOT EXISTS idx_rcv_pay_op_status ON receive_payments(tenant_id, operational_status);
CREATE INDEX IF NOT EXISTS idx_rcv_pay_acc_status ON receive_payments(tenant_id, accounting_status);
