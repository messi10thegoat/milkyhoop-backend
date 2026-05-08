-- V146: Priority constraints + composite index for production_orders
-- Adds CHECK (1-10) and composite index for sort+filter on (tenant_id, status, priority, planned_start_date)
-- Safe: production_orders empty at apply time; column already exists with DEFAULT 5.

BEGIN;

ALTER TABLE production_orders
    ADD CONSTRAINT production_orders_priority_range
    CHECK (priority BETWEEN 1 AND 10);

CREATE INDEX IF NOT EXISTS idx_production_orders_priority_status
    ON production_orders (tenant_id, status, priority, planned_start_date);

COMMIT;
