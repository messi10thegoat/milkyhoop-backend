-- ============================================================
-- V003: Tenant Config Table for Feature Flags
-- ============================================================
-- Purpose: Per-tenant feature flag configuration
-- Allows gradual rollout of atomic function and LISTEN/NOTIFY worker
-- ============================================================

-- Drop existing table if exists (for idempotent migrations)
DROP TABLE IF EXISTS tenant_config CASCADE;

-- ============================================================
-- TENANT CONFIG TABLE
-- ============================================================
CREATE TABLE tenant_config (
    tenant_id TEXT PRIMARY KEY REFERENCES "Tenant"(id) ON DELETE CASCADE,
    -- Feature flags
    use_atomic_function BOOLEAN DEFAULT FALSE,
    use_listen_notify_worker BOOLEAN DEFAULT FALSE,
    -- Worker configuration
    worker_poll_interval_ms INT DEFAULT 2000,
    max_retry_count INT DEFAULT 3,
    batch_size INT DEFAULT 100,
    -- Performance tuning
    enable_telemetry BOOLEAN DEFAULT TRUE,
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- HELPER FUNCTION: Get tenant config with defaults
-- ============================================================
CREATE OR REPLACE FUNCTION get_tenant_config(p_tenant_id TEXT)
RETURNS TABLE (
    use_atomic_function BOOLEAN,
    use_listen_notify_worker BOOLEAN,
    worker_poll_interval_ms INT,
    max_retry_count INT,
    batch_size INT,
    enable_telemetry BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(tc.use_atomic_function, FALSE),
        COALESCE(tc.use_listen_notify_worker, FALSE),
        COALESCE(tc.worker_poll_interval_ms, 2000),
        COALESCE(tc.max_retry_count, 3),
        COALESCE(tc.batch_size, 100),
        COALESCE(tc.enable_telemetry, TRUE)
    FROM tenant_config tc
    WHERE tc.tenant_id = p_tenant_id;

    -- If no config found, return defaults
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT
            FALSE::BOOLEAN,
            FALSE::BOOLEAN,
            2000::INT,
            3::INT,
            100::INT,
            TRUE::BOOLEAN;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- TRIGGER: Auto-update updated_at timestamp
-- ============================================================
CREATE OR REPLACE FUNCTION update_tenant_config_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_tenant_config_updated
    BEFORE UPDATE ON tenant_config
    FOR EACH ROW
    EXECUTE FUNCTION update_tenant_config_timestamp();

-- ============================================================
-- INSERT DEFAULT CONFIGS for existing tenants
-- ============================================================
INSERT INTO tenant_config (tenant_id, use_atomic_function, use_listen_notify_worker)
SELECT id, FALSE, FALSE
FROM "Tenant"
ON CONFLICT (tenant_id) DO NOTHING;

-- ============================================================
-- ENABLE ATOMIC FUNCTION FOR EVLOGIA (test tenant)
-- ============================================================
INSERT INTO tenant_config (tenant_id, use_atomic_function, use_listen_notify_worker, enable_telemetry)
VALUES ('evlogia', TRUE, TRUE, TRUE)
ON CONFLICT (tenant_id) DO UPDATE
SET
    use_atomic_function = TRUE,
    use_listen_notify_worker = TRUE,
    enable_telemetry = TRUE,
    updated_at = NOW();

-- ============================================================
-- GRANT PERMISSIONS
-- ============================================================
GRANT SELECT, INSERT, UPDATE ON tenant_config TO milkyadmin;
GRANT EXECUTE ON FUNCTION get_tenant_config TO milkyadmin;

-- ============================================================
-- VERIFICATION
-- ============================================================
DO $$
DECLARE
    v_evlogia_config RECORD;
BEGIN
    SELECT * INTO v_evlogia_config FROM get_tenant_config('evlogia');
    RAISE NOTICE 'Tenant config table created successfully';
    RAISE NOTICE 'evlogia config: atomic=%, listen_notify=%',
        v_evlogia_config.use_atomic_function,
        v_evlogia_config.use_listen_notify_worker;
END $$;
