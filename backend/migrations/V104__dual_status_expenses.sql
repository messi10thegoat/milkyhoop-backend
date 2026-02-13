-- ============================================================================
-- V104: Dual Status Model for Expenses
-- ============================================================================

ALTER TABLE expenses 
ADD COLUMN IF NOT EXISTS operational_status VARCHAR(20) DEFAULT 'DRAFT',
ADD COLUMN IF NOT EXISTS accounting_status VARCHAR(20) DEFAULT 'UNPOSTED';

ALTER TABLE expenses
ADD CONSTRAINT chk_exp_operational_status CHECK (
    operational_status IN ('DRAFT', 'SUBMITTED', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'PAID', 'VOID')
);

ALTER TABLE expenses
ADD CONSTRAINT chk_exp_accounting_status CHECK (
    accounting_status IN ('UNPOSTED', 'POSTED', 'REVERSED')
);

CREATE INDEX IF NOT EXISTS idx_exp_op_status ON expenses(tenant_id, operational_status);
CREATE INDEX IF NOT EXISTS idx_exp_acc_status ON expenses(tenant_id, accounting_status);
