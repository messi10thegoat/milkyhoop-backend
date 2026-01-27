-- =============================================
-- EVLOGIA SEED: 02_warehouses.sql
-- Purpose: Create 2 warehouses for Evlogia
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating warehouses for tenant: %', v_tenant_id;

    -- Insert warehouses
    INSERT INTO warehouses (
        id, tenant_id, code, name, address, city, phone,
        is_active, is_default, created_at, updated_at
    ) VALUES
    -- Gudang Atput - Main warehouse
    (
        gen_random_uuid(),
        v_tenant_id,
        'WH-ATPUT',
        'Gudang Atput',
        'Jl. Industri Atput No. 88, Kawasan Industri',
        'Bandung',
        '022-12345678',
        true,
        true,  -- Default warehouse
        NOW(),
        NOW()
    ),
    -- Gudang 4A - Showroom/Toko
    (
        gen_random_uuid(),
        v_tenant_id,
        'WH-4A',
        'Gudang 4A (Toko)',
        'Jl. Fashion Boulevard No. 4A, Pusat Perbelanjaan',
        'Bandung',
        '022-87654321',
        true,
        false,
        NOW(),
        NOW()
    )
    ON CONFLICT (tenant_id, code) DO UPDATE SET
        name = EXCLUDED.name,
        address = EXCLUDED.address,
        updated_at = NOW();

    RAISE NOTICE 'Warehouses created: 2';
END $$;

-- Verify
SELECT code, name, is_default FROM warehouses WHERE tenant_id = 'evlogia';
