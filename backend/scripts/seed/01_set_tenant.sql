-- =============================================
-- EVLOGIA SEED: 01_set_tenant.sql
-- Purpose: Set tenant context for all subsequent scripts
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := 'evlogia';
    v_tenant_uuid UUID;
BEGIN
    -- Verify tenant exists and get UUID
    SELECT id INTO v_tenant_uuid
    FROM "Tenant"
    WHERE alias = v_tenant_id;

    IF v_tenant_uuid IS NULL THEN
        RAISE EXCEPTION 'Tenant % tidak ditemukan! Pastikan tenant sudah dibuat.', v_tenant_id;
    END IF;

    -- Set session variable untuk digunakan semua script
    PERFORM set_config('seed.tenant_id', v_tenant_id, false);
    PERFORM set_config('seed.tenant_uuid', v_tenant_uuid::TEXT, false);

    -- Also set RLS context
    PERFORM set_config('app.tenant_id', v_tenant_uuid::TEXT, false);

    RAISE NOTICE '========================================';
    RAISE NOTICE 'TENANT CONTEXT SET';
    RAISE NOTICE 'Tenant Alias: %', v_tenant_id;
    RAISE NOTICE 'Tenant UUID: %', v_tenant_uuid;
    RAISE NOTICE '========================================';
END $$;

-- Verify settings
SELECT
    current_setting('seed.tenant_id', true) as tenant_id,
    current_setting('seed.tenant_uuid', true) as tenant_uuid;
