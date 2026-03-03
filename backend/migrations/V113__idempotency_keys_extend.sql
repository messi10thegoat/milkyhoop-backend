-- P2.3: Extend idempotency_keys table for standardized journal idempotency
-- Law 14: All write operations must be idempotent

-- Add missing columns
ALTER TABLE idempotency_keys
    ADD COLUMN IF NOT EXISTS source_type VARCHAR(50),
    ADD COLUMN IF NOT EXISTS result_id UUID,
    ADD COLUMN IF NOT EXISTS result_status VARCHAR(20) DEFAULT 'SUCCESS';

-- Extend key length (64 is too short for composite keys like "INVOICE_PAYMENT:{uuid}:{amount}:{date}")
ALTER TABLE idempotency_keys ALTER COLUMN key TYPE VARCHAR(255);

-- Add unique constraint for tenant + key
CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency_tenant_key
    ON idempotency_keys(tenant_id, key);

-- Note: Existing usage in bank_reconciliation.py is compatible.
-- New helper: app/utils/idempotency.py
-- Integrated into: sales_invoices.py record_payment
