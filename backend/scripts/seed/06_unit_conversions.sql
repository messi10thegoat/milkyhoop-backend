-- =============================================
-- EVLOGIA SEED: 06_unit_conversions.sql
-- Purpose: Create unit conversions for Kain and Benang products
-- CRITICAL: These are required for proper inventory tracking!
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_product_id UUID;
    v_product_code TEXT;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating unit conversions for tenant: %', v_tenant_id;

    -- ==========================================
    -- KAIN: Roll -> Meter conversions
    -- ==========================================

    -- Katun 30s (1 roll = 50 meter)
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk LIKE 'KTN-%'
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 50, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 roll = 50 meter', v_product_code;
    END LOOP;

    -- Linen (1 roll = 40 meter)
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk LIKE 'LNN-%'
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 40, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 roll = 40 meter', v_product_code;
    END LOOP;

    -- Denim (1 roll = 30 meter)
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk LIKE 'DNM-%'
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 30, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 roll = 30 meter', v_product_code;
    END LOOP;

    -- Batik Cap (1 roll = 25 meter)
    SELECT id INTO v_product_id FROM products
    WHERE tenant_id = v_tenant_id AND kode_produk = 'BTK-001';
    IF v_product_id IS NOT NULL THEN
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 25, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for BTK-001: 1 roll = 25 meter';
    END IF;

    -- Batik Print (1 roll = 50 meter)
    SELECT id INTO v_product_id FROM products
    WHERE tenant_id = v_tenant_id AND kode_produk = 'BTK-002';
    IF v_product_id IS NOT NULL THEN
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 50, true, NOW()
        ) ON CONFLICT DO NOTHING;
    END IF;

    -- Twill (1 roll = 50 meter)
    SELECT id INTO v_product_id FROM products
    WHERE tenant_id = v_tenant_id AND kode_produk = 'TWL-001';
    IF v_product_id IS NOT NULL THEN
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 50, true, NOW()
        ) ON CONFLICT DO NOTHING;
    END IF;

    -- Fleece (1 roll = 40 meter)
    SELECT id INTO v_product_id FROM products
    WHERE tenant_id = v_tenant_id AND kode_produk = 'FLC-001';
    IF v_product_id IS NOT NULL THEN
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'meter', 40, true, NOW()
        ) ON CONFLICT DO NOTHING;
    END IF;

    -- ==========================================
    -- BENANG: Ball -> Lusin -> PCS conversions
    -- Standard: 1 Ball = 12 Lusin = 144 PCS
    -- ==========================================

    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk LIKE 'BNG-%'
        AND kode_produk NOT IN ('BNG-006', 'BNG-007', 'BNG-008') -- These have different ratios
    LOOP
        -- Ball to Lusin (1 ball = 12 lusin)
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'ball', 'lusin', 12, true, NOW()
        ) ON CONFLICT DO NOTHING;

        -- Lusin to PCS (1 lusin = 12 pcs)
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'lusin', 'pcs', 12, true, NOW()
        ) ON CONFLICT DO NOTHING;

        -- Ball to PCS (1 ball = 144 pcs)
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'ball', 'pcs', 144, true, NOW()
        ) ON CONFLICT DO NOTHING;

        RAISE NOTICE 'Unit conversions created for %: 1 ball = 12 lusin = 144 pcs', v_product_code;
    END LOOP;

    -- Benang Bordir & Karet: 1 Ball = 100 PCS (different ratio)
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk IN ('BNG-006', 'BNG-007', 'BNG-008')
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'ball', 'pcs', 100, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 ball = 100 pcs', v_product_code;
    END LOOP;

    -- ==========================================
    -- AKSESORIS: Gross/Pack/Roll conversions
    -- ==========================================

    -- Kancing: 1 gross = 144 pcs
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk LIKE 'KNC-%'
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'gross', 'pcs', 144, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 gross = 144 pcs', v_product_code;
    END LOOP;

    -- Resleting: 1 pack = 10 pcs
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk LIKE 'RSL-%'
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'pack', 'pcs', 10, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 pack = 10 pcs', v_product_code;
    END LOOP;

    -- Label Woven: 1 roll = 500 pcs
    SELECT id INTO v_product_id FROM products
    WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
    IF v_product_id IS NOT NULL THEN
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'roll', 'pcs', 500, true, NOW()
        ) ON CONFLICT DO NOTHING;
    END IF;

    -- Hang Tag, Plastik Pack, Paper Bag: 1 pack = 100 or 50 pcs
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk IN ('LBL-002', 'PKG-001')
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'pack', 'pcs', 100, true, NOW()
        ) ON CONFLICT DO NOTHING;
    END LOOP;

    SELECT id INTO v_product_id FROM products
    WHERE tenant_id = v_tenant_id AND kode_produk = 'PKG-002';
    IF v_product_id IS NOT NULL THEN
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'pack', 'pcs', 50, true, NOW()
        ) ON CONFLICT DO NOTHING;
    END IF;

    -- ==========================================
    -- FG Trading: Lusin -> PCS
    -- ==========================================
    FOR v_product_id, v_product_code IN
        SELECT id, kode_produk FROM products
        WHERE tenant_id = v_tenant_id
        AND kode_produk IN ('IMP-001', 'IMP-002', 'IMP-003', 'IMP-008')
    LOOP
        INSERT INTO unit_conversions (
            id, tenant_id, product_id, from_unit, to_unit, conversion_factor, is_active, created_at
        ) VALUES (
            gen_random_uuid(), v_tenant_id, v_product_id, 'lusin', 'pcs', 12, true, NOW()
        ) ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Unit conversion created for %: 1 lusin = 12 pcs', v_product_code;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Unit conversions completed!';
    RAISE NOTICE '========================================';
END $$;

-- Verify conversions
SELECT
    p.kode_produk,
    p.nama_produk,
    uc.from_unit,
    uc.to_unit,
    uc.conversion_factor
FROM unit_conversions uc
JOIN products p ON uc.product_id = p.id
WHERE uc.tenant_id = 'evlogia'
ORDER BY p.kode_produk, uc.from_unit;
