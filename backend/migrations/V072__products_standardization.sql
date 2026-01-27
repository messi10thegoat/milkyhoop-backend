-- ============================================================================
-- V072: Products Standardization
-- Standardizes price types to BIGINT and adds proper CoA linking
-- Adds SKU, reorder level, and preferred vendor
-- ============================================================================

-- ============================================================================
-- STEP 1: Add new standardized price columns
-- Note: We add new columns instead of altering type to preserve data
-- ============================================================================

-- Add BIGINT price columns
ALTER TABLE products
ADD COLUMN IF NOT EXISTS sales_price_amount BIGINT DEFAULT 0,
ADD COLUMN IF NOT EXISTS purchase_price_amount BIGINT DEFAULT 0;

-- Add CoA linking columns
ALTER TABLE products
ADD COLUMN IF NOT EXISTS sales_account_id UUID,
ADD COLUMN IF NOT EXISTS purchase_account_id UUID,
ADD COLUMN IF NOT EXISTS inventory_account_id UUID;

-- Add additional fields
ALTER TABLE products
ADD COLUMN IF NOT EXISTS sku VARCHAR(50),
ADD COLUMN IF NOT EXISTS reorder_level DECIMAL(10,2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS preferred_vendor_id UUID;

-- ============================================================================
-- STEP 2: Migrate existing price data
-- ============================================================================

-- Copy existing FLOAT prices to BIGINT (rounding to nearest Rupiah)
UPDATE products
SET
    sales_price_amount = COALESCE(ROUND(sales_price)::BIGINT, 0),
    purchase_price_amount = COALESCE(ROUND(purchase_price)::BIGINT, 0)
WHERE sales_price_amount = 0 OR purchase_price_amount = 0;

-- ============================================================================
-- STEP 3: Create indexes
-- ============================================================================

-- SKU index (unique per tenant)
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_tenant_sku
    ON products(tenant_id, sku)
    WHERE sku IS NOT NULL;

-- Sales account index
CREATE INDEX IF NOT EXISTS idx_products_sales_account
    ON products(sales_account_id)
    WHERE sales_account_id IS NOT NULL;

-- Purchase account index
CREATE INDEX IF NOT EXISTS idx_products_purchase_account
    ON products(purchase_account_id)
    WHERE purchase_account_id IS NOT NULL;

-- Preferred vendor index
CREATE INDEX IF NOT EXISTS idx_products_preferred_vendor
    ON products(preferred_vendor_id)
    WHERE preferred_vendor_id IS NOT NULL;

-- Reorder level index (for stock alerts)
CREATE INDEX IF NOT EXISTS idx_products_reorder
    ON products(tenant_id, reorder_level)
    WHERE reorder_level > 0;

-- ============================================================================
-- STEP 4: Link existing accounts to CoA
-- This runs for each tenant that has products
-- ============================================================================

-- Function to link product accounts to CoA
CREATE OR REPLACE FUNCTION link_product_accounts()
RETURNS void AS $$
DECLARE
    v_tenant RECORD;
    v_sales_acc UUID;
    v_purchase_acc UUID;
    v_inventory_acc UUID;
BEGIN
    FOR v_tenant IN SELECT DISTINCT tenant_id FROM products LOOP
        -- Get default Sales account (4-10100)
        SELECT id INTO v_sales_acc
        FROM chart_of_accounts
        WHERE tenant_id = v_tenant.tenant_id
          AND account_code = '4-10100'
        LIMIT 1;

        -- Get default COGS account (5-10100)
        SELECT id INTO v_purchase_acc
        FROM chart_of_accounts
        WHERE tenant_id = v_tenant.tenant_id
          AND account_code = '5-10100'
        LIMIT 1;

        -- Get default Inventory account (1-10400)
        SELECT id INTO v_inventory_acc
        FROM chart_of_accounts
        WHERE tenant_id = v_tenant.tenant_id
          AND account_code = '1-10400'
        LIMIT 1;

        -- Update products without account links
        UPDATE products
        SET
            sales_account_id = COALESCE(sales_account_id, v_sales_acc),
            purchase_account_id = COALESCE(purchase_account_id, v_purchase_acc),
            inventory_account_id = COALESCE(inventory_account_id, v_inventory_acc)
        WHERE tenant_id = v_tenant.tenant_id
          AND (sales_account_id IS NULL OR purchase_account_id IS NULL OR inventory_account_id IS NULL);
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Run the linking function
SELECT link_product_accounts();

-- ============================================================================
-- STEP 5: Add Hutang PPh account if not exists
-- ============================================================================

-- Insert Hutang PPh account for all tenants that have CoA
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, parent_code, is_active)
SELECT DISTINCT
    tenant_id,
    '2-10500' as account_code,
    'Hutang PPh' as name,
    'LIABILITY' as account_type,
    '2-10000' as parent_code,
    true as is_active
FROM chart_of_accounts
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c2
    WHERE c2.tenant_id = chart_of_accounts.tenant_id
    AND c2.account_code = '2-10500'
)
GROUP BY tenant_id
ON CONFLICT DO NOTHING;

-- Insert PPN Masukan account for all tenants that have CoA
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, parent_code, is_active)
SELECT DISTINCT
    tenant_id,
    '1-10700' as account_code,
    'PPN Masukan' as name,
    'ASSET' as account_type,
    '1-10000' as parent_code,
    true as is_active
FROM chart_of_accounts
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c2
    WHERE c2.tenant_id = chart_of_accounts.tenant_id
    AND c2.account_code = '1-10700'
)
GROUP BY tenant_id
ON CONFLICT DO NOTHING;

-- Insert PPN Keluaran account for all tenants that have CoA
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, parent_code, is_active)
SELECT DISTINCT
    tenant_id,
    '2-10600' as account_code,
    'PPN Keluaran' as name,
    'LIABILITY' as account_type,
    '2-10000' as parent_code,
    true as is_active
FROM chart_of_accounts
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c2
    WHERE c2.tenant_id = chart_of_accounts.tenant_id
    AND c2.account_code = '2-10600'
)
GROUP BY tenant_id
ON CONFLICT DO NOTHING;

-- ============================================================================
-- STEP 6: Create view for unified price access
-- ============================================================================
CREATE OR REPLACE VIEW v_products_with_prices AS
SELECT
    p.*,
    -- Use new BIGINT columns, fallback to old FLOAT columns
    COALESCE(p.sales_price_amount, ROUND(p.sales_price)::BIGINT, 0) as effective_sales_price,
    COALESCE(p.purchase_price_amount, ROUND(p.purchase_price)::BIGINT, 0) as effective_purchase_price,
    -- Include account names
    sa.name as sales_account_name,
    pa.name as purchase_account_name,
    ia.name as inventory_account_name,
    -- Include vendor name
    v.name as preferred_vendor_name
FROM products p
LEFT JOIN chart_of_accounts sa ON p.sales_account_id = sa.id
LEFT JOIN chart_of_accounts pa ON p.purchase_account_id = pa.id
LEFT JOIN chart_of_accounts ia ON p.inventory_account_id = ia.id
LEFT JOIN vendors v ON p.preferred_vendor_id = v.id;

-- ============================================================================
-- STEP 7: Function to get product with full details
-- ============================================================================
CREATE OR REPLACE FUNCTION get_product_details(p_product_id UUID)
RETURNS TABLE (
    id UUID,
    tenant_id TEXT,
    nama_produk VARCHAR,
    sku VARCHAR,
    barcode VARCHAR,
    item_type VARCHAR,
    sales_price BIGINT,
    purchase_price BIGINT,
    sales_account_id UUID,
    sales_account_name VARCHAR,
    purchase_account_id UUID,
    purchase_account_name VARCHAR,
    inventory_account_id UUID,
    inventory_account_name VARCHAR,
    preferred_vendor_id UUID,
    preferred_vendor_name VARCHAR,
    reorder_level DECIMAL,
    track_inventory BOOLEAN,
    is_active BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        p.id,
        p.tenant_id,
        p.nama_produk,
        p.sku,
        p.barcode,
        p.item_type,
        COALESCE(p.sales_price_amount, ROUND(p.sales_price)::BIGINT, 0) as sales_price,
        COALESCE(p.purchase_price_amount, ROUND(p.purchase_price)::BIGINT, 0) as purchase_price,
        p.sales_account_id,
        sa.name as sales_account_name,
        p.purchase_account_id,
        pa.name as purchase_account_name,
        p.inventory_account_id,
        ia.name as inventory_account_name,
        p.preferred_vendor_id,
        v.name as preferred_vendor_name,
        p.reorder_level,
        p.track_inventory,
        p.is_active
    FROM products p
    LEFT JOIN chart_of_accounts sa ON p.sales_account_id = sa.id
    LEFT JOIN chart_of_accounts pa ON p.purchase_account_id = pa.id
    LEFT JOIN chart_of_accounts ia ON p.inventory_account_id = ia.id
    LEFT JOIN vendors v ON p.preferred_vendor_id = v.id
    WHERE p.id = p_product_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON COLUMN products.sales_price_amount IS 'Harga jual dalam Rupiah (BIGINT) - replaces FLOAT sales_price';
COMMENT ON COLUMN products.purchase_price_amount IS 'Harga beli dalam Rupiah (BIGINT) - replaces FLOAT purchase_price';
COMMENT ON COLUMN products.sales_account_id IS 'FK to chart_of_accounts - Akun Penjualan (4-xxxxx)';
COMMENT ON COLUMN products.purchase_account_id IS 'FK to chart_of_accounts - Akun HPP (5-xxxxx)';
COMMENT ON COLUMN products.inventory_account_id IS 'FK to chart_of_accounts - Akun Persediaan (1-10400)';
COMMENT ON COLUMN products.sku IS 'Stock Keeping Unit - internal product code (unique per tenant)';
COMMENT ON COLUMN products.reorder_level IS 'Minimum stock level to trigger reorder alert';
COMMENT ON COLUMN products.preferred_vendor_id IS 'Default vendor for this product (FK vendors)';

COMMENT ON VIEW v_products_with_prices IS 'Products view with unified price access and account names';
COMMENT ON FUNCTION get_product_details IS 'Get full product details including account and vendor names';
COMMENT ON FUNCTION link_product_accounts IS 'Links existing products to default CoA accounts';

-- ============================================================================
-- CLEANUP: Drop helper function (no longer needed after migration)
-- ============================================================================
DROP FUNCTION IF EXISTS link_product_accounts();
