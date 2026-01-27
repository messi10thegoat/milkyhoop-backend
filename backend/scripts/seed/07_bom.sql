-- =============================================
-- EVLOGIA SEED: 07_bom.sql
-- Purpose: Create Bill of Materials for 8 FG Produksi items
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_bom_id UUID;
    v_fg_id UUID;
    v_component_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating BOMs for tenant: %', v_tenant_id;

    -- ==========================================
    -- BOM 1: Kemeja Evlogia Slim Fit Putih (EVL-001)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-001';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-001', 'BOM Kemeja Slim Fit Putih', '1.0', 1,
            35000, 10000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Components
        -- Kain Katun 30s Putih: 1.5 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KTN-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1.5, 'meter', 5, 'Badan + lengan kemeja');

        -- Benang Jahit Putih: 2 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2, 'pcs', 0, 'Benang jahit utama');

        -- Kancing Kemeja Putih: 8 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KNC-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 8, 'pcs', 10, '7 badan + 1 spare');

        -- Label Evlogia: 1 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, 'Label leher');

        -- Hang Tag: 1 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, 'Hang tag brand');

        -- Plastik Pack: 1 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'PKG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, 'Plastik kemasan');

        RAISE NOTICE 'BOM created: Kemeja Slim Fit Putih (6 components)';
    END IF;

    -- ==========================================
    -- BOM 2: Kemeja Evlogia Regular Biru (EVL-002)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-002';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-002', 'BOM Kemeja Regular Biru', '1.0', 1,
            35000, 10000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain Katun Navy
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KTN-003';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1.7, 'meter', 5, 'Regular fit butuh lebih bahan');

        -- Benang Navy
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-003';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2, 'pcs', 0, NULL);

        -- Kancing Hitam
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KNC-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 8, 'pcs', 10, NULL);

        -- Label + Hang Tag + Plastik
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'PKG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        RAISE NOTICE 'BOM created: Kemeja Regular Biru (6 components)';
    END IF;

    -- ==========================================
    -- BOM 3: Dress Batik Evlogia A-Line (EVL-003)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-003';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-003', 'BOM Dress Batik A-Line', '1.0', 1,
            55000, 15000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain Batik Cap Jogja: 2.5 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BTK-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2.5, 'meter', 5, 'Badan dress A-line');

        -- Benang Jahit: 3 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 3, 'pcs', 0, NULL);

        -- Resleting 50cm: 1 pcs
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'RSL-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, 'Resleting belakang');

        -- Label
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        RAISE NOTICE 'BOM created: Dress Batik A-Line (4 components)';
    END IF;

    -- ==========================================
    -- BOM 4: Dress Batik Evlogia Modern (EVL-004)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-004';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-004', 'BOM Dress Batik Modern', '1.0', 1,
            45000, 12000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain Batik Print: 2.3 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BTK-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2.3, 'meter', 5, NULL);

        -- Benang
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2, 'pcs', 0, NULL);

        -- Resleting 20cm
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'RSL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        -- Label
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        RAISE NOTICE 'BOM created: Dress Batik Modern (4 components)';
    END IF;

    -- ==========================================
    -- BOM 5: Celana Chino Evlogia Khaki (EVL-005)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-005';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-005', 'BOM Celana Chino Khaki', '1.0', 1,
            40000, 10000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain Twill Khaki: 1.2 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'TWL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1.2, 'meter', 5, NULL);

        -- Benang
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2, 'pcs', 0, NULL);

        -- Resleting Celana 15cm
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'RSL-003';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        -- Kancing Celana
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KNC-003';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        -- Label
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        RAISE NOTICE 'BOM created: Celana Chino (5 components)';
    END IF;

    -- ==========================================
    -- BOM 6: Blazer Evlogia Wanita (EVL-006)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-006';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-006', 'BOM Blazer Wanita', '1.0', 1,
            75000, 20000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain Katun 30s Hitam: 2.0 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KTN-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2.0, 'meter', 5, 'Badan + lengan');

        -- Benang Hitam
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 3, 'pcs', 0, NULL);

        -- Kancing
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KNC-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 3, 'pcs', 10, 'Kancing depan');

        -- Label
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        RAISE NOTICE 'BOM created: Blazer Wanita (4 components)';
    END IF;

    -- ==========================================
    -- BOM 7: Seragam Kantor Evlogia Pria (EVL-007)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-007';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-007', 'BOM Seragam Kantor Pria', '1.0', 1,
            50000, 15000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain untuk kemeja + celana: 2.8 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KTN-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2.8, 'meter', 5, 'Kemeja 1.5m + Celana 1.3m');

        -- Benang
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 4, 'pcs', 0, NULL);

        -- Kancing Kemeja
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KNC-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 8, 'pcs', 10, NULL);

        -- Resleting Celana
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'RSL-003';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        -- Kancing Celana
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'KNC-003';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        -- Label
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2, 'pcs', 0, '2 label: kemeja + celana');

        RAISE NOTICE 'BOM created: Seragam Kantor Pria (6 components)';
    END IF;

    -- ==========================================
    -- BOM 8: Hoodie Evlogia Premium (EVL-008)
    -- ==========================================
    SELECT id INTO v_fg_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'EVL-008';

    IF v_fg_id IS NOT NULL THEN
        v_bom_id := gen_random_uuid();

        INSERT INTO bill_of_materials (
            id, tenant_id, product_id, bom_code, bom_name, version, unit_yield,
            labor_cost, overhead_cost, is_active, created_at, updated_at
        ) VALUES (
            v_bom_id, v_tenant_id, v_fg_id,
            'BOM-EVL-008', 'BOM Hoodie Premium', '1.0', 1,
            60000, 18000, true, NOW(), NOW()
        ) ON CONFLICT DO NOTHING;

        -- Kain Fleece: 2.2 meter
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'FLC-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2.2, 'meter', 5, 'Badan + hood + lengan');

        -- Benang
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 3, 'pcs', 0, NULL);

        -- Benang Karet (untuk pinggang)
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'BNG-008';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 2, 'pcs', 0, 'Karet pinggang + manset');

        -- Resleting depan
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'RSL-002';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, 'Resleting depan full');

        -- Label
        SELECT id INTO v_component_id FROM products WHERE tenant_id = v_tenant_id AND kode_produk = 'LBL-001';
        INSERT INTO bom_components (id, bom_id, product_id, quantity, unit, waste_percent, notes)
        VALUES (gen_random_uuid(), v_bom_id, v_component_id, 1, 'pcs', 0, NULL);

        RAISE NOTICE 'BOM created: Hoodie Premium (5 components)';
    END IF;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'BOMs completed: 8 finished goods';
    RAISE NOTICE '========================================';
END $$;

-- Verify BOMs
SELECT
    bom.bom_code,
    bom.bom_name,
    p.nama_produk as finished_good,
    COUNT(bc.id) as component_count,
    bom.labor_cost,
    bom.overhead_cost
FROM bill_of_materials bom
JOIN products p ON bom.product_id = p.id
LEFT JOIN bom_components bc ON bc.bom_id = bom.id
WHERE bom.tenant_id = 'evlogia'
GROUP BY bom.id, bom.bom_code, bom.bom_name, p.nama_produk, bom.labor_cost, bom.overhead_cost
ORDER BY bom.bom_code;

-- Detail components for one BOM
SELECT
    bom.bom_code,
    p.kode_produk as component_code,
    p.nama_produk as component_name,
    bc.quantity,
    bc.unit,
    bc.waste_percent
FROM bom_components bc
JOIN bill_of_materials bom ON bc.bom_id = bom.id
JOIN products p ON bc.product_id = p.id
WHERE bom.tenant_id = 'evlogia' AND bom.bom_code = 'BOM-EVL-001'
ORDER BY bc.id;
