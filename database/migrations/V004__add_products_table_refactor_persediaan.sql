-- Migration V004: Add Products table and refactor Persediaan
-- Sprint 2.1: Smart Product Resolution
-- Created: 2025-11-18

-- ============================================
-- STEP 1: Create Products table (master data)
-- ============================================
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(255) NOT NULL,
    nama_produk VARCHAR(100) NOT NULL,
    satuan VARCHAR(50) NOT NULL,
    kategori VARCHAR(100),
    harga_jual DECIMAL(15, 2),
    deskripsi TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    CONSTRAINT products_tenant_nama_unique UNIQUE (tenant_id, nama_produk),
    CONSTRAINT fk_products_tenant FOREIGN KEY (tenant_id) REFERENCES public."Tenant"(id) ON DELETE CASCADE
);

-- Indexes for Products
CREATE INDEX idx_products_tenant ON products(tenant_id);
CREATE INDEX idx_products_tenant_nama ON products(tenant_id, nama_produk);

-- ============================================
-- STEP 2: Migrate existing Persediaan data to Products
-- ============================================
-- For each unique (tenant_id, produk_id), create a Product entry
-- SKIP UUID-based produkId (legacy data), only migrate product names

INSERT INTO products (tenant_id, nama_produk, satuan, created_at, updated_at)
SELECT DISTINCT
    p.tenant_id,
    p.produk_id as nama_produk,
    COALESCE(p.satuan, 'pcs') as satuan,
    NOW(),
    NOW()
FROM persediaan p
WHERE
    -- Skip UUID-based produkId (length 36 with hyphens)
    p.produk_id NOT LIKE '%-%'
    AND p.produk_id NOT SIMILAR TO '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
ON CONFLICT (tenant_id, nama_produk) DO NOTHING;

-- ============================================
-- STEP 3: Add product_id column to Persediaan (nullable for now)
-- ============================================
ALTER TABLE persediaan
ADD COLUMN IF NOT EXISTS product_id UUID REFERENCES products(id) ON DELETE CASCADE;

-- ============================================
-- STEP 4: Populate product_id in Persediaan from Products
-- ============================================
-- Link existing Persediaan entries to Products table
UPDATE persediaan p
SET product_id = prod.id
FROM products prod
WHERE
    p.tenant_id = prod.tenant_id
    AND p.produk_id = prod.nama_produk
    AND p.product_id IS NULL;

-- ============================================
-- STEP 5: Drop old produk_id column and constraints
-- ============================================
-- Drop old unique constraint
ALTER TABLE persediaan DROP CONSTRAINT IF EXISTS "persediaan_tenantId_produkId_lokasiGudang_key";

-- NOTE: We keep produk_id column for backward compatibility with old data
-- In Phase 2, after all data is migrated, we can drop it:
-- ALTER TABLE persediaan DROP COLUMN produk_id;

-- ============================================
-- STEP 6: Create new unique constraint with product_id
-- ============================================
-- Ensure one stock record per (tenant, product, warehouse)
CREATE UNIQUE INDEX IF NOT EXISTS persediaan_tenant_product_warehouse_unique
ON persediaan(tenant_id, product_id, lokasi_gudang)
WHERE product_id IS NOT NULL;

-- ============================================
-- STEP 7: Add index for product_id
-- ============================================
CREATE INDEX IF NOT EXISTS idx_persediaan_product ON persediaan(product_id);

-- ============================================
-- NOTES FOR FUTURE:
-- ============================================
-- After confirming all new transactions use Products:
-- 1. Make product_id NOT NULL
-- 2. Drop produk_id column completely
-- 3. Update all services to use Products table
--
-- For now, both columns exist for backward compatibility:
-- - produk_id (VARCHAR): old system (UUID or product name)
-- - product_id (UUID FK): new system (references Products.id)
-- ============================================