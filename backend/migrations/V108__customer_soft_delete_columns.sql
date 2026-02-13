-- V100: Add soft-delete audit columns to customers table
-- is_active already exists (V024), adding deleted_at and deleted_by for audit trail

ALTER TABLE customers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(255);

COMMENT ON COLUMN customers.deleted_at IS 'Timestamp when customer was soft-deleted';
COMMENT ON COLUMN customers.deleted_by IS 'User ID who performed the soft-delete';

-- Partial index for efficient filtering of active customers
CREATE INDEX IF NOT EXISTS idx_customers_deleted_at ON customers(tenant_id, deleted_at)
    WHERE deleted_at IS NOT NULL;
