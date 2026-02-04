-- V092__customer_activities.sql
-- Customer Activity Log: audit trail for customer mutations
-- Pattern follows vendor_activities (V091) and item_activities (V080)

CREATE TABLE IF NOT EXISTS customer_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id VARCHAR NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    type VARCHAR(50) NOT NULL,  -- 'created', 'updated', 'status_changed', etc.
    description VARCHAR(255) NOT NULL,
    details TEXT,
    actor_id UUID,
    actor_name VARCHAR(255),
    field_name VARCHAR(100),  -- for field-level changes
    old_value TEXT,
    new_value TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Indexes for performance
CREATE INDEX idx_customer_activities_customer_id ON customer_activities(customer_id);
CREATE INDEX idx_customer_activities_tenant_id ON customer_activities(tenant_id);
CREATE INDEX idx_customer_activities_timestamp ON customer_activities(timestamp DESC);

COMMENT ON TABLE customer_activities IS 'Audit trail for customer entity changes';
