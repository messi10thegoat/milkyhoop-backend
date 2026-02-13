-- ============================================================================
-- V102: Dual Status Model for Bills
-- ============================================================================

ALTER TABLE bills 
ADD COLUMN IF NOT EXISTS operational_status VARCHAR(20) DEFAULT 'DRAFT',
ADD COLUMN IF NOT EXISTS accounting_status VARCHAR(20) DEFAULT 'UNPOSTED';

ALTER TABLE bills
ADD CONSTRAINT chk_bill_operational_status CHECK (
    operational_status IN ('DRAFT', 'RECEIVED', 'PENDING_APPROVAL', 'APPROVED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE', 'DISPUTED', 'VOID')
);

ALTER TABLE bills
ADD CONSTRAINT chk_bill_accounting_status CHECK (
    accounting_status IN ('UNPOSTED', 'POSTED', 'REVERSED')
);

CREATE INDEX IF NOT EXISTS idx_bills_op_status ON bills(tenant_id, operational_status);
CREATE INDEX IF NOT EXISTS idx_bills_acc_status ON bills(tenant_id, accounting_status);
