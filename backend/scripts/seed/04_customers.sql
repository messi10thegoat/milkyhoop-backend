-- =============================================
-- EVLOGIA SEED: 04_customers.sql
-- Purpose: Create 25 customers for Fashion & Textile business
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating customers for tenant: %', v_tenant_id;

    INSERT INTO customers (
        id, tenant_id, code, name, contact_person, phone, email,
        address, city, province, tax_id, payment_terms_days,
        credit_limit, is_active, created_at, updated_at
    ) VALUES
    -- ==========================================
    -- RESELLER / GROSIR (10)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'CST-001', 'Toko Baju Murah Jaya',
        'Bapak Herman', '0812-1111-0001', 'tokobajumurah@gmail.com',
        'Pasar Tanah Abang Blok A Lt. 2 No. 45',
        'Jakarta Pusat', 'DKI Jakarta', '01.111.111.1-091.000',
        30, 100000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-002', 'CV Fashion Grosir Bandung',
        'Ibu Rina', '022-2222002', 'fashiongrosir@gmail.com',
        'Jl. Otto Iskandardinata No. 88',
        'Bandung', 'Jawa Barat', '02.222.222.2-423.000',
        30, 75000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-003', 'UD Textile Makmur Sejahtera',
        'Bapak Agus', '031-3333003', 'textilemakmur@yahoo.com',
        'Jl. Kapasan No. 123',
        'Surabaya', 'Jawa Timur', '03.333.333.3-601.000',
        30, 80000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-004', 'Toko Kain Berkah',
        'Ibu Sari', '0813-4444-0004', 'kainberkah@gmail.com',
        'Pasar Tekstil Tanah Abang Lt. 3 No. 12',
        'Jakarta Pusat', 'DKI Jakarta', NULL,
        14, 50000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-005', 'CV Busana Prima',
        'Bapak Dedi', '024-5555005', 'busanaprima@gmail.com',
        'Jl. Pandanaran No. 56',
        'Semarang', 'Jawa Tengah', '04.444.444.4-501.000',
        30, 60000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-006', 'Toko Pakaian Maju Jaya',
        'Ibu Lina', '0274-6666006', 'pakaianjaya@gmail.com',
        'Jl. Malioboro No. 78',
        'Yogyakarta', 'DI Yogyakarta', NULL,
        14, 40000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-007', 'CV Konveksi Mandiri',
        'Bapak Rudi', '022-7777007', 'konveksimandiri@gmail.com',
        'Jl. Cibaduyut Raya No. 200',
        'Bandung', 'Jawa Barat', '05.555.555.5-423.000',
        14, 100000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-008', 'UD Garmen Sejahtera',
        'Ibu Dewi', '0341-8888008', 'garmensejahtera@yahoo.com',
        'Jl. Veteran No. 45',
        'Malang', 'Jawa Timur', NULL,
        30, 55000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-009', 'Toko Fashion Corner',
        'Bapak Andy', '061-9999009', 'fashioncorner@gmail.com',
        'Jl. Asia Mega Mas No. 25',
        'Medan', 'Sumatera Utara', '06.666.666.6-101.000',
        30, 70000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-010', 'CV Distro Keren Abis',
        'Ibu Nia', '0812-1010-1010', 'distrokeren@gmail.com',
        'Jl. Dago No. 150',
        'Bandung', 'Jawa Barat', '07.777.777.7-423.000',
        14, 45000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- BOUTIQUE / TOKO PREMIUM (5)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'CST-011', 'Butik Elegant Jakarta',
        'Ibu Kartika', '021-1111011', 'elegant@butikjakarta.com',
        'Plaza Indonesia Lt. 3 No. 28',
        'Jakarta Pusat', 'DKI Jakarta', '08.888.888.8-091.000',
        14, 50000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-012', 'Chic & Style Boutique',
        'Ibu Miranda', '021-1212012', 'miranda@chicstyle.co.id',
        'Grand Indonesia West Mall Lt. 2',
        'Jakarta Pusat', 'DKI Jakarta', '09.999.999.9-091.000',
        14, 40000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-013', 'Galeri Batik Nusantara',
        'Ibu Ratna', '0274-1313013', 'galeribatik@nusantara.com',
        'Jl. Solo No. 89',
        'Yogyakarta', 'DI Yogyakarta', '10.101.010.1-501.000',
        21, 35000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-014', 'House of Fashion Surabaya',
        'Ibu Vina', '031-1414014', 'houseoffashion@gmail.com',
        'Tunjungan Plaza 5 Lt. 2',
        'Surabaya', 'Jawa Timur', '11.111.111.1-601.000',
        14, 30000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-015', 'Ladiva Fashion House',
        'Ibu Diana', '022-1515015', 'ladiva@fashionhouse.com',
        'Paris Van Java Mall Lt. 2',
        'Bandung', 'Jawa Barat', '12.121.212.1-423.000',
        14, 25000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- CORPORATE / SERAGAM (5)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'CST-016', 'PT Hotel Bintang Lima Indonesia',
        'Bapak Jonathan', '021-1616016', 'procurement@hotelbintanglima.co.id',
        'Jl. Thamrin No. 1',
        'Jakarta Pusat', 'DKI Jakarta', '13.131.313.1-091.000',
        45, 500000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-017', 'PT Bank Nasional Indonesia',
        'Ibu Shinta', '021-1717017', 'uniform@banknasional.co.id',
        'Jl. Sudirman Kav. 50',
        'Jakarta Selatan', 'DKI Jakarta', '14.141.414.1-091.000',
        45, 750000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-018', 'RS Sehat Sejahtera',
        'Ibu Nurse Ani', '021-1818018', 'purchasing@rssehat.co.id',
        'Jl. Gatot Subroto No. 100',
        'Jakarta Selatan', 'DKI Jakarta', '15.151.515.1-091.000',
        30, 200000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-019', 'PT Asuransi Terpercaya',
        'Bapak Budi', '021-1919019', 'budi@asuransiterpercaya.co.id',
        'Jl. HR Rasuna Said Kav. C-5',
        'Jakarta Selatan', 'DKI Jakarta', '16.161.616.1-091.000',
        30, 150000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-020', 'Universitas Negeri Bandung',
        'Bapak Dr. Hendra', '022-2020020', 'pengadaan@unb.ac.id',
        'Jl. Setiabudhi No. 229',
        'Bandung', 'Jawa Barat', '17.171.717.1-423.000',
        30, 100000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- RETAIL / WALK-IN / CASH (5)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'CST-021', 'Customer Umum',
        NULL, NULL, NULL,
        'Walk-in Customer',
        'Bandung', 'Jawa Barat', NULL,
        0, 0, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-022', 'Cash Customer',
        NULL, NULL, NULL,
        'Walk-in Cash Sales',
        'Bandung', 'Jawa Barat', NULL,
        0, 0, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-023', 'Online Customer - Shopee',
        'CS Shopee', NULL, 'evlogia@shopee.co.id',
        'Marketplace Shopee',
        'Online', 'Online', NULL,
        0, 0, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-024', 'Online Customer - Tokopedia',
        'CS Tokopedia', NULL, 'evlogia@tokopedia.com',
        'Marketplace Tokopedia',
        'Online', 'Online', NULL,
        0, 0, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'CST-025', 'Online Customer - Instagram',
        'Admin IG', '0812-2525-2525', 'evlogia.fashion@instagram.com',
        'Social Media Instagram @evlogia.fashion',
        'Online', 'Online', NULL,
        0, 0, true, NOW(), NOW()
    )
    ON CONFLICT (tenant_id, code) DO UPDATE SET
        name = EXCLUDED.name,
        contact_person = EXCLUDED.contact_person,
        phone = EXCLUDED.phone,
        email = EXCLUDED.email,
        payment_terms_days = EXCLUDED.payment_terms_days,
        credit_limit = EXCLUDED.credit_limit,
        updated_at = NOW();

    RAISE NOTICE 'Customers created: 25';
END $$;

-- Verify
SELECT code, name, payment_terms_days, credit_limit FROM customers WHERE tenant_id = 'evlogia' ORDER BY code;
