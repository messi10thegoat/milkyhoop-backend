-- =============================================
-- EVLOGIA SEED: 05_products.sql
-- Purpose: Create 50 products for Fashion & Textile business
-- Categories: Kain (10), Benang (8), Aksesoris (10), FG Trading (10), FG Produksi (8), Services (4)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating products for tenant: %', v_tenant_id;

    INSERT INTO products (
        id, tenant_id, kode_produk, nama_produk, kategori, satuan,
        item_type, track_inventory, purchase_price, sales_price,
        base_unit, wholesale_unit, units_per_wholesale,
        description, is_active, created_at, updated_at
    ) VALUES
    -- ==========================================
    -- BAHAN KAIN (10 items)
    -- Base unit: meter, Buy unit: roll
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'KTN-001', 'Kain Katun 30s Putih', 'Bahan Kain', 'meter',
        'goods', true, 35000, 55000,
        'meter', 'roll', 50,
        'Kain katun combed 30s warna putih, lebar 150cm, 1 roll = 50 meter',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'KTN-002', 'Kain Katun 30s Hitam', 'Bahan Kain', 'meter',
        'goods', true, 37000, 58000,
        'meter', 'roll', 50,
        'Kain katun combed 30s warna hitam, lebar 150cm',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'KTN-003', 'Kain Katun 24s Navy', 'Bahan Kain', 'meter',
        'goods', true, 32000, 50000,
        'meter', 'roll', 50,
        'Kain katun combed 24s warna navy blue',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'LNN-001', 'Kain Linen Premium Natural', 'Bahan Kain', 'meter',
        'goods', true, 85000, 125000,
        'meter', 'roll', 40,
        'Kain linen premium warna natural/krem, lebar 140cm',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'DNM-001', 'Kain Denim 12oz Blue', 'Bahan Kain', 'meter',
        'goods', true, 65000, 95000,
        'meter', 'roll', 30,
        'Kain denim 12oz warna blue wash, lebar 150cm',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'DNM-002', 'Kain Denim 10oz Black', 'Bahan Kain', 'meter',
        'goods', true, 58000, 85000,
        'meter', 'roll', 30,
        'Kain denim 10oz warna hitam, lebar 150cm',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BTK-001', 'Kain Batik Cap Jogja Parang', 'Bahan Kain', 'meter',
        'goods', true, 75000, 120000,
        'meter', 'roll', 25,
        'Kain batik cap motif parang klasik Jogja',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BTK-002', 'Kain Batik Print Modern', 'Bahan Kain', 'meter',
        'goods', true, 45000, 75000,
        'meter', 'roll', 50,
        'Kain batik print motif modern kontemporer',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'TWL-001', 'Kain Katun Twill Khaki', 'Bahan Kain', 'meter',
        'goods', true, 42000, 65000,
        'meter', 'roll', 50,
        'Kain katun twill untuk celana chino, warna khaki',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'FLC-001', 'Kain Fleece Premium Grey', 'Bahan Kain', 'meter',
        'goods', true, 55000, 85000,
        'meter', 'roll', 40,
        'Kain fleece tebal untuk jaket/hoodie, warna abu-abu',
        true, NOW(), NOW()
    ),

    -- ==========================================
    -- BAHAN BENANG (8 items)
    -- Base unit: pcs, Buy unit: ball (144 pcs)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-001', 'Benang Jahit Polyester Putih', 'Bahan Benang', 'pcs',
        'goods', true, 2500, 4000,
        'pcs', 'ball', 144,
        'Benang jahit polyester putih, 1 ball = 12 lusin = 144 pcs',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-002', 'Benang Jahit Polyester Hitam', 'Bahan Benang', 'pcs',
        'goods', true, 2500, 4000,
        'pcs', 'ball', 144,
        'Benang jahit polyester hitam',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-003', 'Benang Jahit Polyester Navy', 'Bahan Benang', 'pcs',
        'goods', true, 2700, 4200,
        'pcs', 'ball', 144,
        'Benang jahit polyester warna navy',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-004', 'Benang Obras Putih', 'Bahan Benang', 'pcs',
        'goods', true, 3000, 4500,
        'pcs', 'ball', 144,
        'Benang obras/overlock putih',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-005', 'Benang Obras Hitam', 'Bahan Benang', 'pcs',
        'goods', true, 3000, 4500,
        'pcs', 'ball', 144,
        'Benang obras/overlock hitam',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-006', 'Benang Bordir Emas', 'Bahan Benang', 'pcs',
        'goods', true, 5000, 8000,
        'pcs', 'ball', 100,
        'Benang bordir metalik warna emas',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-007', 'Benang Bordir Silver', 'Bahan Benang', 'pcs',
        'goods', true, 5000, 8000,
        'pcs', 'ball', 100,
        'Benang bordir metalik warna silver',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'BNG-008', 'Benang Karet Elastis', 'Bahan Benang', 'pcs',
        'goods', true, 3500, 5500,
        'pcs', 'ball', 100,
        'Benang karet elastis untuk pinggang',
        true, NOW(), NOW()
    ),

    -- ==========================================
    -- AKSESORIS (10 items)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'KNC-001', 'Kancing Kemeja Putih 10mm', 'Aksesoris', 'pcs',
        'goods', true, 150, 300,
        'pcs', 'gross', 144,
        'Kancing kemeja plastik putih diameter 10mm, 1 gross = 144 pcs',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'KNC-002', 'Kancing Kemeja Hitam 10mm', 'Aksesoris', 'pcs',
        'goods', true, 150, 300,
        'pcs', 'gross', 144,
        'Kancing kemeja plastik hitam diameter 10mm',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'KNC-003', 'Kancing Celana Metal 15mm', 'Aksesoris', 'pcs',
        'goods', true, 500, 850,
        'pcs', 'gross', 144,
        'Kancing celana jeans metal, diameter 15mm',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'RSL-001', 'Resleting YKK 20cm Hitam', 'Aksesoris', 'pcs',
        'goods', true, 3500, 5500,
        'pcs', 'pack', 10,
        'Resleting YKK 20cm warna hitam, 1 pack = 10 pcs',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'RSL-002', 'Resleting YKK 50cm Hitam', 'Aksesoris', 'pcs',
        'goods', true, 7500, 12000,
        'pcs', 'pack', 10,
        'Resleting YKK 50cm untuk dress/jaket',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'RSL-003', 'Resleting YKK 15cm Celana', 'Aksesoris', 'pcs',
        'goods', true, 2800, 4500,
        'pcs', 'pack', 10,
        'Resleting YKK 15cm khusus celana',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'LBL-001', 'Label Evlogia Woven', 'Aksesoris', 'pcs',
        'goods', true, 500, 850,
        'pcs', 'roll', 500,
        'Label woven brand Evlogia, 1 roll = 500 pcs',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'LBL-002', 'Hang Tag Evlogia', 'Aksesoris', 'pcs',
        'goods', true, 350, 600,
        'pcs', 'pack', 100,
        'Hang tag karton brand Evlogia dengan string',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'PKG-001', 'Plastik Pack Kemeja', 'Aksesoris', 'pcs',
        'goods', true, 200, 350,
        'pcs', 'pack', 100,
        'Plastik OPP untuk packing kemeja',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'PKG-002', 'Paper Bag Evlogia Medium', 'Aksesoris', 'pcs',
        'goods', true, 2500, 4000,
        'pcs', 'pack', 50,
        'Paper bag branded Evlogia ukuran medium',
        true, NOW(), NOW()
    ),

    -- ==========================================
    -- FINISHED GOODS - TRADING / IMPORT (10 items)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-001', 'Kaos Polos Cotton Combed 30s Putih', 'FG Trading', 'pcs',
        'goods', true, 35000, 65000,
        'pcs', 'lusin', 12,
        'Kaos polos import cotton combed 30s putih, 1 lusin = 12 pcs',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-002', 'Kaos Polos Cotton Combed 30s Hitam', 'FG Trading', 'pcs',
        'goods', true, 35000, 65000,
        'pcs', 'lusin', 12,
        'Kaos polos import cotton combed 30s hitam',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-003', 'Kaos Polos Cotton Combed 30s Navy', 'FG Trading', 'pcs',
        'goods', true, 35000, 65000,
        'pcs', 'lusin', 12,
        'Kaos polos import cotton combed 30s navy',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-004', 'Jaket Hoodie Fleece Import', 'FG Trading', 'pcs',
        'goods', true, 125000, 225000,
        'pcs', 'pcs', 1,
        'Jaket hoodie fleece import premium',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-005', 'Celana Jeans Standar Blue', 'FG Trading', 'pcs',
        'goods', true, 95000, 175000,
        'pcs', 'pcs', 1,
        'Celana jeans standar warna blue wash',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-006', 'Celana Jeans Standar Black', 'FG Trading', 'pcs',
        'goods', true, 95000, 175000,
        'pcs', 'pcs', 1,
        'Celana jeans standar warna hitam',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-007', 'Kemeja Flanel Import Kotak', 'FG Trading', 'pcs',
        'goods', true, 85000, 150000,
        'pcs', 'pcs', 1,
        'Kemeja flanel import motif kotak-kotak',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-008', 'Polo Shirt Import Putih', 'FG Trading', 'pcs',
        'goods', true, 75000, 135000,
        'pcs', 'lusin', 12,
        'Polo shirt import warna putih',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-009', 'Cardigan Rajut Import', 'FG Trading', 'pcs',
        'goods', true, 110000, 195000,
        'pcs', 'pcs', 1,
        'Cardigan rajut import wanita',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'IMP-010', 'Rok Span Import Hitam', 'FG Trading', 'pcs',
        'goods', true, 65000, 120000,
        'pcs', 'pcs', 1,
        'Rok span formal import warna hitam',
        true, NOW(), NOW()
    ),

    -- ==========================================
    -- FINISHED GOODS - PRODUKSI SENDIRI (8 items)
    -- These will have BOMs
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-001', 'Kemeja Evlogia Slim Fit Putih', 'FG Produksi', 'pcs',
        'goods', true, 0, 275000,  -- purchase_price 0 karena produksi sendiri
        'pcs', 'pcs', 1,
        'Kemeja slim fit brand Evlogia, bahan katun 30s putih',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-002', 'Kemeja Evlogia Regular Biru', 'FG Produksi', 'pcs',
        'goods', true, 0, 265000,
        'pcs', 'pcs', 1,
        'Kemeja regular fit brand Evlogia, bahan katun navy',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-003', 'Dress Batik Evlogia A-Line', 'FG Produksi', 'pcs',
        'goods', true, 0, 450000,
        'pcs', 'pcs', 1,
        'Dress batik A-line brand Evlogia, batik cap Jogja',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-004', 'Dress Batik Evlogia Modern', 'FG Produksi', 'pcs',
        'goods', true, 0, 385000,
        'pcs', 'pcs', 1,
        'Dress batik modern brand Evlogia, batik print',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-005', 'Celana Chino Evlogia Khaki', 'FG Produksi', 'pcs',
        'goods', true, 0, 295000,
        'pcs', 'pcs', 1,
        'Celana chino brand Evlogia, bahan twill khaki',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-006', 'Blazer Evlogia Wanita', 'FG Produksi', 'pcs',
        'goods', true, 0, 525000,
        'pcs', 'pcs', 1,
        'Blazer formal wanita brand Evlogia',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-007', 'Seragam Kantor Evlogia Pria', 'FG Produksi', 'pcs',
        'goods', true, 0, 245000,
        'pcs', 'pcs', 1,
        'Seragam kantor pria brand Evlogia (kemeja + celana)',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'EVL-008', 'Hoodie Evlogia Premium', 'FG Produksi', 'pcs',
        'goods', true, 0, 325000,
        'pcs', 'pcs', 1,
        'Hoodie premium brand Evlogia, bahan fleece',
        true, NOW(), NOW()
    ),

    -- ==========================================
    -- SERVICES (4 items)
    -- ==========================================
    (
        gen_random_uuid(), v_tenant_id,
        'SVC-001', 'Jasa Jahit Kemeja', 'Services', 'pcs',
        'service', false, 0, 75000,
        'pcs', 'pcs', 1,
        'Jasa jahit kemeja (bahan dari customer)',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'SVC-002', 'Jasa Jahit Dress', 'Services', 'pcs',
        'service', false, 0, 150000,
        'pcs', 'pcs', 1,
        'Jasa jahit dress (bahan dari customer)',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'SVC-003', 'Jasa Jahit Celana', 'Services', 'pcs',
        'service', false, 0, 60000,
        'pcs', 'pcs', 1,
        'Jasa jahit celana (bahan dari customer)',
        true, NOW(), NOW()
    ),
    (
        gen_random_uuid(), v_tenant_id,
        'SVC-004', 'Jasa Bordir Logo', 'Services', 'pcs',
        'service', false, 0, 25000,
        'pcs', 'pcs', 1,
        'Jasa bordir logo per 1000 stitch',
        true, NOW(), NOW()
    )
    ON CONFLICT (tenant_id, kode_produk) DO UPDATE SET
        nama_produk = EXCLUDED.nama_produk,
        kategori = EXCLUDED.kategori,
        purchase_price = EXCLUDED.purchase_price,
        sales_price = EXCLUDED.sales_price,
        base_unit = EXCLUDED.base_unit,
        wholesale_unit = EXCLUDED.wholesale_unit,
        units_per_wholesale = EXCLUDED.units_per_wholesale,
        description = EXCLUDED.description,
        updated_at = NOW();

    RAISE NOTICE 'Products created: 50';
END $$;

-- Verify by category
SELECT kategori, COUNT(*) as count
FROM products
WHERE tenant_id = 'evlogia'
GROUP BY kategori
ORDER BY kategori;

-- Sample products
SELECT kode_produk, nama_produk, satuan, purchase_price, sales_price
FROM products
WHERE tenant_id = 'evlogia'
ORDER BY kode_produk
LIMIT 10;
