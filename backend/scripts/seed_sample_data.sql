-- ============================================
-- SAMPLE PRODUCTS FOR AUTOCOMPLETE TESTING
-- ============================================

-- Indomie variants
INSERT INTO products (id, tenant_id, nama_produk, satuan, kategori, created_at, updated_at) VALUES
('prod_indomie_goreng_orig', 'evlogia', 'Indomie Goreng Original', 'pcs', 'Mi Instan', NOW(), NOW()),
('prod_indomie_kuah_ayam', 'evlogia', 'Indomie Kuah Ayam Spesial', 'pcs', 'Mi Instan', NOW(), NOW()),
('prod_indomie_goreng_rendang', 'evlogia', 'Indomie Goreng Rendang', 'pcs', 'Mi Instan', NOW(), NOW()),
('prod_indomie_goreng_pedas', 'evlogia', 'Indomie Goreng Pedas', 'pcs', 'Mi Instan', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- Dettol variants
INSERT INTO products (id, tenant_id, nama_produk, satuan, kategori, created_at, updated_at) VALUES
('prod_dettol_original_410g', 'evlogia', 'Dettol Body Wash Original 410g', 'pcs', 'Alat Mandi', NOW(), NOW()),
('prod_dettol_fresh_410ml', 'evlogia', 'Dettol Body Wash Fresh 410ml', 'pcs', 'Alat Mandi', NOW(), NOW()),
('prod_dettol_cool_500ml', 'evlogia', 'Dettol Body Wash Cool 500ml', 'pcs', 'Alat Mandi', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- Lux variants
INSERT INTO products (id, tenant_id, nama_produk, satuan, kategori, created_at, updated_at) VALUES
('prod_lux_sakura_825ml', 'evlogia', 'Lux Body Wash Sakura Bloom 825ml', 'pcs', 'Alat Mandi', NOW(), NOW()),
('prod_lux_soft_rose_500ml', 'evlogia', 'Lux Body Wash Soft Rose 500ml', 'pcs', 'Alat Mandi', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- Additional products
INSERT INTO products (id, tenant_id, nama_produk, satuan, kategori, created_at, updated_at) VALUES
('prod_aqua_600ml', 'evlogia', 'Aqua Botol 600ml', 'pcs', 'Minuman', NOW(), NOW()),
('prod_kopi_kapal_api', 'evlogia', 'Kopi Kapal Api Special', 'pcs', 'Kopi', NOW(), NOW()),
('prod_gula_pasir_1kg', 'evlogia', 'Gula Pasir 1kg', 'kg', 'Bahan Pokok', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- SAMPLE SUPPLIERS FOR AUTOCOMPLETE TESTING
-- ============================================

INSERT INTO suppliers (id, tenant_id, nama_supplier, kontak, alamat, created_at, updated_at) VALUES
('supp_indogrosir', 'evlogia', 'Indogrosir', '021-1234567', 'Jl. Grosir Raya No. 1, Jakarta', NOW(), NOW()),
('supp_indo_mandiri', 'evlogia', 'CV. Indo Mandiri', '0811-222-3333', 'Jl. Mandiri Blok A No. 5, Bandung', NOW(), NOW()),
('supp_dmb', 'evlogia', 'Distribusi Murah Bandung', '022-9876543', 'Jl. Distribusi No. 10, Bandung', NOW(), NOW()),
('supp_toko_jaya', 'evlogia', 'Toko Jaya Makmur', '0812-333-4444', 'Pasar Induk Blok C-12', NOW(), NOW()),
('supp_central_grosir', 'evlogia', 'Central Grosir Indonesia', '021-5555666', 'Jl. Central No. 88, Jakarta Utara', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- Verify data was inserted
-- ============================================
SELECT 'Products count: ' || COUNT(*) FROM products WHERE tenant_id = 'evlogia';
SELECT 'Suppliers count: ' || COUNT(*) FROM suppliers WHERE tenant_id = 'evlogia';
