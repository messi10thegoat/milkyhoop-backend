-- =============================================
-- EVLOGIA SEED: 03_vendors.sql
-- Purpose: Create 15 vendors for Fashion & Textile business
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating vendors for tenant: %', v_tenant_id;

    INSERT INTO vendors (
        id, tenant_id, code, name, contact_person, phone, email,
        address, city, province, tax_id, payment_terms_days,
        credit_limit, is_active, created_at, updated_at
    ) VALUES
    -- ==========================================
    -- SUPPLIER KAIN (5)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'VND-001', 'PT Textile Indonesia Jaya',
        'Bapak Hartono', '021-5551001', 'sales@textileindonesia.co.id',
        'Jl. Raya Tekstil No. 100, Kawasan Industri Jababeka',
        'Cikarang', 'Jawa Barat', '01.234.567.8-091.000',
        30, 500000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-002', 'CV Kain Makmur Sentosa',
        'Ibu Siti Aminah', '022-5552002', 'kainmakmur@gmail.com',
        'Jl. Cigondewah Raya No. 45',
        'Bandung', 'Jawa Barat', '02.345.678.9-423.000',
        14, 200000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-003', 'PT Batik Pekalongan Indah',
        'Bapak Suryo', '0285-5553003', 'order@batikpekalongan.com',
        'Jl. Batik Raya No. 77, Pekalongan Utara',
        'Pekalongan', 'Jawa Tengah', '03.456.789.0-501.000',
        30, 300000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-004', 'UD Kain Murah Jaya',
        'Bapak Asep', '022-5554004', 'kainmurahjaya@yahoo.com',
        'Pasar Baru Trade Center Lt. 2 No. 25',
        'Bandung', 'Jawa Barat', NULL,
        7, 50000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-005', 'PT Denim Indo Perkasa',
        'Ibu Linda', '021-5555005', 'linda@denimindo.co.id',
        'Jl. Industri Denim No. 8',
        'Tangerang', 'Banten', '04.567.890.1-411.000',
        30, 400000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- SUPPLIER BENANG (3)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'VND-006', 'PT Benang Emas Nusantara',
        'Bapak Wijaya', '021-5556006', 'sales@benangemas.co.id',
        'Jl. Industri Tekstil Blok C No. 12',
        'Cibitung', 'Jawa Barat', '05.678.901.2-091.000',
        14, 150000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-007', 'CV Thread Master Indonesia',
        'Bapak Tommy', '031-5557007', 'threadmaster@gmail.com',
        'Jl. SIER Blok W No. 55',
        'Surabaya', 'Jawa Timur', '06.789.012.3-601.000',
        14, 100000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-008', 'Toko Benang 99',
        'Ibu Mei Ling', '022-5558008', NULL,
        'Pasar Tekstil Cigondewah Blok A No. 99',
        'Bandung', 'Jawa Barat', NULL,
        7, 30000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- SUPPLIER AKSESORIS (3)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'VND-009', 'PT YKK Zipper Indonesia',
        'Bapak Tanaka', '021-5559009', 'order@ykk.co.id',
        'Jl. Jababeka XVII Blok U No. 35',
        'Cikarang', 'Jawa Barat', '07.890.123.4-091.000',
        21, 250000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-010', 'CV Kancing Jaya Abadi',
        'Ibu Dewi', '022-5550010', 'kancingjaya@gmail.com',
        'Jl. Soekarno Hatta No. 567',
        'Bandung', 'Jawa Barat', '08.901.234.5-423.000',
        14, 75000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-011', 'PT Label Woven Indonesia',
        'Bapak Eko', '021-5550011', 'sales@labelwoven.com',
        'Kawasan Industri Pulogadung Blok II No. 8',
        'Jakarta Timur', 'DKI Jakarta', '09.012.345.6-091.000',
        21, 100000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- SUPPLIER BAJU JADI / IMPORT (2)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'VND-012', 'PT Garment Import Nusantara',
        'Bapak Steven', '021-5550012', 'steven@garmentimport.co.id',
        'Jl. Mangga Dua Raya No. 88',
        'Jakarta Utara', 'DKI Jakarta', '10.123.456.7-091.000',
        30, 750000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-013', 'CV Fashion Wholesale Center',
        'Ibu Grace', '021-5550013', 'grace@fashionwholesale.com',
        'ITC Mangga Dua Lt. 5 Blok C No. 10-15',
        'Jakarta Utara', 'DKI Jakarta', '11.234.567.8-091.000',
        14, 300000000, true, NOW(), NOW()
    ),

    -- ==========================================
    -- JASA MAKLON / KONVEKSI (2)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'VND-014', 'CV Konveksi Sukses Mandiri',
        'Bapak Dadang', '022-5550014', 'konveksisukses@gmail.com',
        'Jl. Cimahi Selatan No. 123',
        'Cimahi', 'Jawa Barat', '12.345.678.9-423.000',
        7, 100000000, true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'VND-015', 'UD Jahit Rapi Berkah',
        'Ibu Yanti', '0812-3456-7890', NULL,
        'Jl. Pasirkaliki No. 45',
        'Bandung', 'Jawa Barat', NULL,
        7, 50000000, true, NOW(), NOW()
    )
    ON CONFLICT (tenant_id, code) DO UPDATE SET
        name = EXCLUDED.name,
        contact_person = EXCLUDED.contact_person,
        phone = EXCLUDED.phone,
        email = EXCLUDED.email,
        payment_terms_days = EXCLUDED.payment_terms_days,
        credit_limit = EXCLUDED.credit_limit,
        updated_at = NOW();

    RAISE NOTICE 'Vendors created: 15';
END $$;

-- Verify
SELECT code, name, payment_terms_days, credit_limit FROM vendors WHERE tenant_id = 'evlogia' ORDER BY code;
