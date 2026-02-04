-- V091__vendor_activities.sql
-- Vendor Activity Log: audit trail for vendor mutations

CREATE TABLE IF NOT EXISTS vendor_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
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

CREATE INDEX idx_vendor_activities_vendor_id ON vendor_activities(vendor_id);
CREATE INDEX idx_vendor_activities_tenant_id ON vendor_activities(tenant_id);
CREATE INDEX idx_vendor_activities_timestamp ON vendor_activities(timestamp DESC);
