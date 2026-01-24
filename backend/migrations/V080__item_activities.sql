-- V080__item_activities.sql
-- Item Activity Log: audit trail for item mutations

CREATE TABLE IF NOT EXISTS item_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    type VARCHAR(20) NOT NULL,
    description VARCHAR(255) NOT NULL,
    details TEXT,
    actor_id UUID,
    actor_name VARCHAR(255),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_item_activities_item_id ON item_activities(item_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_item_activities_tenant ON item_activities(tenant_id, item_id);

-- Backfill existing items with 'created' activity
INSERT INTO item_activities (item_id, tenant_id, type, description, timestamp)
SELECT id, tenant_id, 'created', 'Item dibuat', COALESCE(created_at, NOW())
FROM products
WHERE id NOT IN (SELECT DISTINCT item_id FROM item_activities);
