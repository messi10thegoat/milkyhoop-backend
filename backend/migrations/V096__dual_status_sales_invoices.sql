-- ============================================================================
-- V096: Dual Status Model for Sales Invoices
-- Separates operational_status from accounting_status
-- ============================================================================

-- Add dual status columns
ALTER TABLE sales_invoices 
ADD COLUMN IF NOT EXISTS operational_status VARCHAR(20) DEFAULT 'DRAFT',
ADD COLUMN IF NOT EXISTS accounting_status VARCHAR(20) DEFAULT 'UNPOSTED';

-- Constraints for operational status
ALTER TABLE sales_invoices
ADD CONSTRAINT chk_inv_operational_status CHECK (
    operational_status IN ('DRAFT', 'SENT', 'VIEWED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE', 'DISPUTED', 'VOID')
);

-- Constraints for accounting status
ALTER TABLE sales_invoices
ADD CONSTRAINT chk_inv_accounting_status CHECK (
    accounting_status IN ('UNPOSTED', 'POSTED', 'REVERSED')
);

-- Indexes for efficient filtering
CREATE INDEX IF NOT EXISTS idx_sales_inv_op_status ON sales_invoices(tenant_id, operational_status);
CREATE INDEX IF NOT EXISTS idx_sales_inv_acc_status ON sales_invoices(tenant_id, accounting_status);

-- Combined index for common query patterns
CREATE INDEX IF NOT EXISTS idx_sales_inv_dual_status ON sales_invoices(tenant_id, operational_status, accounting_status);
