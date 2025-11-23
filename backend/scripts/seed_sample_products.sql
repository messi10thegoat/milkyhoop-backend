-- ============================================
-- SEED SAMPLE PRODUCTS & SUPPLIERS
-- Tenant: evlogia
--
-- Approach: Insert dummy transactions to populate
-- item_transaksi and transaksi_harian tables
-- ============================================

-- Create a temporary user ID for seeded transactions
-- First, check if we have a user for evlogia tenant
DO $$
DECLARE
    v_user_id UUID;
    v_tenant_id TEXT := 'evlogia';
    v_tx_id TEXT;
    v_timestamp BIGINT;
BEGIN
    -- Get existing user for evlogia tenant
    SELECT id INTO v_user_id FROM public."User" WHERE "tenantId" = v_tenant_id LIMIT 1;

    IF v_user_id IS NULL THEN
        RAISE NOTICE 'No user found for tenant evlogia, skipping seed';
        RETURN;
    END IF;

    -- Set base timestamp (current time)
    v_timestamp := EXTRACT(EPOCH FROM NOW()) * 1000;

    -- ============================================
    -- SEED SUPPLIERS via transaksi_harian
    -- ============================================

    -- Supplier 1: Indogrosir
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, kontak_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'Indogrosir', '0541-7771604', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Supplier 2: CV. Indo Mandiri
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 1000, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'CV. Indo Mandiri', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Supplier 3: Distribusi Murah Bandung
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 2000, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'Distribusi Murah Bandung', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Supplier 4: PT. Indo Marco
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 3000, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'PT. Indo Marco - Pusat Grosir', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Supplier 5: PT. Arta Boga Cemerlang
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 4000, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'PT. Arta Boga Cemerlang', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Supplier 6: PT. Expand Semesta Jaya
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 5000, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'PT. Expand Semesta Jaya', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Supplier 7: Indomaret
    v_tx_id := 'seed_supp_' || substr(md5(random()::text), 1, 12);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 6000, 'pembelian',
        '{"items": [{"name": "Seed Product", "quantity": 1, "unit": "pcs", "unit_price": 1000, "subtotal": 1000}]}',
        'approved', 1000, 'Indomaret', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    RAISE NOTICE 'Suppliers seeded successfully';
END $$;

-- ============================================
-- SEED PRODUCTS via item_transaksi
-- We need parent transaksi_harian records first
-- ============================================

DO $$
DECLARE
    v_user_id UUID;
    v_tenant_id TEXT := 'evlogia';
    v_tx_id TEXT;
    v_timestamp BIGINT;
    v_product RECORD;
BEGIN
    -- Get existing user for evlogia tenant
    SELECT id INTO v_user_id FROM public."User" WHERE "tenantId" = v_tenant_id LIMIT 1;

    IF v_user_id IS NULL THEN
        RAISE NOTICE 'No user found for tenant evlogia, skipping product seed';
        RETURN;
    END IF;

    v_timestamp := EXTRACT(EPOCH FROM NOW()) * 1000;

    -- Create a parent transaction for all products
    v_tx_id := 'seed_prod_parent_' || substr(md5(random()::text), 1, 8);
    INSERT INTO public.transaksi_harian (
        id, tenant_id, created_by, actor_role, timestamp, jenis_transaksi,
        payload, status, total_nominal, nama_pihak, pihak_type
    ) VALUES (
        v_tx_id, v_tenant_id, v_user_id, 'owner', v_timestamp - 10000, 'pembelian',
        '{}', 'approved', 0, 'Seed Supplier', 'supplier'
    ) ON CONFLICT (id) DO NOTHING;

    -- Insert products as item_transaksi
    -- INDOMIE VARIANTS
    FOR v_product IN
        SELECT * FROM (VALUES
            ('Indomie Goreng Original', 'pcs', 3500),
            ('Indomie Kuah Ayam Spesial', 'pcs', 3500),
            ('Indomie Goreng Rendang', 'pcs', 3800),
            ('Indomie Ayam Spesial', 'pcs', 3500),
            ('Indomie Mix 4 Rasa', 'pcs', 14000),
            ('Indomie Mix 8 Varian', 'pcs', 28000),
            ('Indomie Goreng Soto', 'pcs', 3500),
            ('Indomie Rebus', 'pcs', 3200),
            ('Dettol Body Wash Original 410g', 'pcs', 45000),
            ('Dettol Body Wash Fresh 410ml', 'pcs', 45000),
            ('Dettol Body Wash Cool 500ml', 'pcs', 52000),
            ('Dettol Body Wash Lasting Fresh 800g', 'pcs', 78000),
            ('Dettol Body Wash Sensitive 410g', 'pcs', 45000),
            ('Dettol Body Wash Onzen Hachimitsu 410g', 'pcs', 48000),
            ('Dettol Body Wash Re-energize 410g', 'pcs', 45000),
            ('Dettol Body Wash Skin Care 410g', 'pcs', 45000),
            ('Dettol Body Wash Tropical Splash 410g', 'pcs', 45000),
            ('Dettol Body Wash Botanical Green Tea 370g', 'pcs', 42000),
            ('Dettol Body Wash Botanical Lavender 370g', 'pcs', 42000),
            ('Dettol Bar Soap ProFresh 105g', 'pcs', 8500),
            ('Dettol Cool Refill 800ml', 'pcs', 72000),
            ('Dettol Family Protect 370g', 'pcs', 40000),
            ('Nuvo Family Antiseptic Mild 400ml', 'pcs', 35000),
            ('Nuvo Active Cool 400ml', 'pcs', 35000),
            ('Lux Body Wash Sakura Bloom 825ml', 'pcs', 68000),
            ('Lux Body Wash Soft Rose 500ml', 'pcs', 42000),
            ('Lux Body Wash Hijab Olive Honey 400ml', 'pcs', 38000),
            ('Lux Body Wash Hijab Lavender Chamomile 400ml', 'pcs', 38000),
            ('Lux French Rose & Almond Oil', 'pcs', 45000),
            ('Lux Body Wash Bluebell Refill 900ml', 'pcs', 72000),
            ('Lux Botanicals Magical Orchid 450ml', 'pcs', 42000),
            ('Lux Botanicals Magical Orchid 825ml', 'pcs', 68000),
            ('Lux Botanicals Bird of Paradise', 'pcs', 45000),
            ('Lux Botanicals Soft Rose 825ml', 'pcs', 68000),
            ('Lux Botanicals Camellia White', 'pcs', 45000),
            ('Lux Botanicals Blue Peony', 'pcs', 45000),
            ('Lux Botanicals Blue Bell', 'pcs', 45000),
            ('Lux Botanicals Lily Fresh', 'pcs', 45000),
            ('Lux Botanicals Sandalwood Musk', 'pcs', 45000),
            ('Lux Botanicals Yuzu Blossom', 'pcs', 45000),
            ('Lux Botanicals Hijab Zaitun & Madu', 'pcs', 38000),
            ('Lux Velvet Jasmine 825ml', 'pcs', 68000),
            ('Nivea Sabun Creme Care Batangan', 'pcs', 12000),
            ('Nivea Body Wash Creme Smooth 750ml', 'pcs', 65000),
            ('Nivea Aloe Cream Shower 750ml', 'pcs', 65000),
            ('Nivea Energy Shower Gel 250ml', 'pcs', 35000),
            ('Nivea Men Shower Gel Sport 500ml', 'pcs', 48000),
            ('Nivea Lemongrass & Oil Shower Gel', 'pcs', 45000),
            ('Nivea Waterlily & Oil Shower Gel', 'pcs', 45000),
            ('Nivea Frangipani & Oil Shower Gel', 'pcs', 45000),
            ('Nivea Cashmere Moments Cream Oil Shower', 'pcs', 48000),
            ('Nivea Diamond Touch Cream Oil Shower', 'pcs', 48000),
            ('Lifebuoy Anti Bakteri Mild Care 250ml', 'pcs', 25000),
            ('Lifebuoy Japanese Shiso & Sandalwood 400ml', 'pcs', 38000),
            ('Lifebuoy Lemon Fresh 250ml', 'pcs', 25000),
            ('Lifebuoy Lemon Fresh Refill 400g', 'pcs', 35000),
            ('Lifebuoy Bar Soap Kasturi Musk 110g', 'pcs', 8000),
            ('Lifebuoy Bar Soap Lemon Fresh 110g', 'pcs', 8000),
            ('Biore Body Wash Floral Spa 450ml', 'pcs', 42000),
            ('Biore Body Wash Relaxing Aromatic 450ml', 'pcs', 42000),
            ('Biore Body Wash Pure Mild 450ml', 'pcs', 42000),
            ('Biore Body Wash Clear Fresh 450ml', 'pcs', 42000),
            ('Biore Body Wash Bright Scrub 450ml', 'pcs', 45000),
            ('Biore Body Wash Bright Sakura 450ml', 'pcs', 45000),
            ('Biore Body Wash Energetic Cool 220ml', 'pcs', 25000),
            ('Biore Body Wash White Scrub 220ml', 'pcs', 25000),
            ('Biore Body Wash Glow Up Lilac 220ml', 'pcs', 25000),
            ('Biore Body Wash Active Merah 220ml', 'pcs', 25000),
            ('Biore Body Wash Hygienic 220ml', 'pcs', 25000),
            ('Biore Lovely Sakura Body Wash 800ml', 'pcs', 72000),
            ('Biore Guard Active Antibacterial 800ml', 'pcs', 72000),
            ('Shinzui Body Soap Kirei 450ml', 'pcs', 38000),
            ('Shinzui Skin Lightening Myori 480ml', 'pcs', 42000),
            ('Shinzui Body Cleanser Refill 380ml', 'pcs', 32000),
            ('Shinzui Body Cleanser Refill 725ml', 'pcs', 58000),
            ('Shinzui Body Scrub Hana', 'pcs', 35000),
            ('Dove Deeply Nourishing Body Wash 400ml', 'pcs', 48000),
            ('Dove Go Fresh Revive Pomegranate 550ml', 'pcs', 55000),
            ('Dove Go Fresh Touch Cucumber 550ml', 'pcs', 55000),
            ('Giv Body Wash Tin & Zaitun 400ml', 'pcs', 28000),
            ('Giv Body Wash Mulberry & Collagen 400ml', 'pcs', 28000),
            ('Giv Body Wash Saffron & Niacinamide 400ml', 'pcs', 28000),
            ('Giv Body Wash White Soap 400ml', 'pcs', 28000),
            ('Pepsodent Complete 8 Siwak 65g', 'pcs', 8500),
            ('Pepsodent Complete 8 Siwak 110g', 'pcs', 12000),
            ('Pepsodent Complete 8 Siwak 150g', 'pcs', 16000),
            ('Pepsodent Complete 8 Multi-Protection 150g', 'pcs', 18000),
            ('Safeguard Body Wash Pure White 720ml', 'pcs', 62000),
            ('Safeguard Body Wash Grapefruit 720ml', 'pcs', 62000),
            ('Safeguard White Camellia 1000ml', 'pcs', 78000),
            ('Cindynal Goat Milk Niacinamide 800ml', 'pcs', 85000),
            ('Secret Garden Balinese Oryza Body Gel', 'pcs', 95000),
            ('The Body Shop British Rose Shower Gel 250ml', 'pcs', 189000)
        ) AS t(nama_produk, satuan, harga_satuan)
    LOOP
        INSERT INTO public.item_transaksi (
            id, transaksi_id, nama_produk, satuan, harga_satuan, jumlah, subtotal, created_at, updated_at
        ) VALUES (
            'item_' || substr(md5(random()::text), 1, 12),
            v_tx_id,
            v_product.nama_produk,
            v_product.satuan,
            v_product.harga_satuan,
            1,
            v_product.harga_satuan,
            NOW(),
            NOW()
        ) ON CONFLICT DO NOTHING;
    END LOOP;

    RAISE NOTICE 'Products seeded successfully';
END $$;

-- ============================================
-- VERIFY SEED RESULTS
-- ============================================
SELECT 'Suppliers' as type, COUNT(DISTINCT nama_pihak) as count
FROM public.transaksi_harian
WHERE tenant_id = 'evlogia' AND nama_pihak IS NOT NULL AND nama_pihak != '';

SELECT 'Products' as type, COUNT(DISTINCT nama_produk) as count
FROM public.item_transaksi it
JOIN public.transaksi_harian th ON it.transaksi_id = th.id
WHERE th.tenant_id = 'evlogia';
