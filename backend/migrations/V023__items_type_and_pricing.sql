-- ============================================
-- V023: Add Item Types & Multi-Pricing Support
-- ============================================
-- Purpose: Support goods vs services, track_inventory toggle,
-- flexible unit conversions, and multi-tier pricing
--
-- New columns on products:
--   item_type          - 'goods' or 'service'
--   track_inventory    - boolean, whether to track stock
--   is_returnable      - boolean, whether item can be returned
--   purchase_price     - base purchase price
--   sales_price        - base sales price (replaces harga_jual usage)
--   sales_account      - default sales account
--   purchase_account   - default purchase account
--   sales_tax          - default sales tax code
--   purchase_tax       - default purchase tax code
--
-- New tables:
--   unit_conversions   - flexible unit conversion mappings
--   item_pricing       - multi-tier pricing per unit
-- ============================================

-- Step 1: Add new columns to products table
ALTER TABLE products ADD COLUMN IF NOT EXISTS item_type VARCHAR(20) DEFAULT 'goods';
ALTER TABLE products ADD COLUMN IF NOT EXISTS track_inventory BOOLEAN DEFAULT true;
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_returnable BOOLEAN DEFAULT true;
ALTER TABLE products ADD COLUMN IF NOT EXISTS purchase_price FLOAT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_price FLOAT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_account VARCHAR(100) DEFAULT 'Sales';
ALTER TABLE products ADD COLUMN IF NOT EXISTS purchase_account VARCHAR(100) DEFAULT 'Cost of Goods Sold';
ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_tax VARCHAR(50);
ALTER TABLE products ADD COLUMN IF NOT EXISTS purchase_tax VARCHAR(50);

-- Step 2: Add constraint for item_type (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_products_item_type'
    ) THEN
        ALTER TABLE products ADD CONSTRAINT chk_products_item_type
            CHECK (item_type IN ('goods', 'service'));
    END IF;
END $$;

-- Step 3: Create index for track_inventory filtering
CREATE INDEX IF NOT EXISTS idx_products_track_inventory
ON products (tenant_id, track_inventory)
WHERE track_inventory = true;

-- Step 4: Create index for item_type filtering
CREATE INDEX IF NOT EXISTS idx_products_item_type
ON products (tenant_id, item_type);

-- Step 5: Create unit_conversions table
CREATE TABLE IF NOT EXISTS unit_conversions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    product_id UUID NOT NULL,

    -- Base unit (smallest selling unit)
    base_unit VARCHAR(50) NOT NULL,

    -- Conversion unit and factor
    conversion_unit VARCHAR(50) NOT NULL,
    conversion_factor INT NOT NULL CHECK (conversion_factor > 0),

    -- Pricing for this unit
    purchase_price FLOAT,
    sales_price FLOAT,

    -- Status
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Foreign key
    CONSTRAINT fk_uc_product FOREIGN KEY (product_id)
        REFERENCES products(id) ON DELETE CASCADE,

    -- Unique constraint per product/unit combo
    CONSTRAINT uq_unit_conversion UNIQUE(tenant_id, product_id, base_unit, conversion_unit)
);

-- Indexes for unit_conversions
CREATE INDEX IF NOT EXISTS idx_uc_product ON unit_conversions(product_id);
CREATE INDEX IF NOT EXISTS idx_uc_tenant ON unit_conversions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_uc_active ON unit_conversions(tenant_id, is_active)
    WHERE is_active = true;

-- Step 6: Create item_pricing table for flexible pricing tiers
CREATE TABLE IF NOT EXISTS item_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    product_id UUID NOT NULL,

    -- Pricing info
    unit VARCHAR(50) NOT NULL,
    pricing_type VARCHAR(20) NOT NULL CHECK (pricing_type IN ('purchase', 'sales')),
    price FLOAT NOT NULL CHECK (price >= 0),

    -- Optional tier conditions
    min_quantity INT,
    max_quantity INT,

    -- Status
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Foreign key
    CONSTRAINT fk_ip_product FOREIGN KEY (product_id)
        REFERENCES products(id) ON DELETE CASCADE,

    -- Unique active pricing per product/unit/type
    CONSTRAINT uq_item_pricing UNIQUE(tenant_id, product_id, unit, pricing_type)
);

-- Indexes for item_pricing
CREATE INDEX IF NOT EXISTS idx_ip_product_type ON item_pricing(product_id, pricing_type, is_active);
CREATE INDEX IF NOT EXISTS idx_ip_tenant ON item_pricing(tenant_id);

-- Step 7: Migrate existing V007 unit conversion data to new table
INSERT INTO unit_conversions (tenant_id, product_id, base_unit, conversion_unit, conversion_factor)
SELECT
    p.tenant_id,
    p.id,
    COALESCE(p.base_unit, 'pcs'),
    p.wholesale_unit,
    p.units_per_wholesale
FROM products p
WHERE p.wholesale_unit IS NOT NULL
  AND p.units_per_wholesale IS NOT NULL
  AND p.units_per_wholesale > 0
ON CONFLICT (tenant_id, product_id, base_unit, conversion_unit) DO NOTHING;

-- Step 8: Backfill sales_price from harga_jual
UPDATE products
SET sales_price = harga_jual
WHERE harga_jual IS NOT NULL AND sales_price IS NULL;

-- Step 9: Backfill all existing products as goods with track_inventory
UPDATE products
SET
    item_type = 'goods',
    track_inventory = true
WHERE item_type IS NULL;

-- Step 10: Add comments for documentation
COMMENT ON COLUMN products.item_type IS 'Type of item: goods (physical) or service';
COMMENT ON COLUMN products.track_inventory IS 'Whether to track stock levels for this item';
COMMENT ON COLUMN products.is_returnable IS 'Whether this item can be returned (goods only)';
COMMENT ON COLUMN products.sales_account IS 'Default income account for sales';
COMMENT ON COLUMN products.purchase_account IS 'Default expense/COGS account for purchases';

COMMENT ON TABLE unit_conversions IS 'Maps unit conversions for products (e.g., 1 dus = 12 pcs)';
COMMENT ON COLUMN unit_conversions.base_unit IS 'Smallest unit: pcs, botol, lembar';
COMMENT ON COLUMN unit_conversions.conversion_unit IS 'Larger unit: dus, karton, pack';
COMMENT ON COLUMN unit_conversions.conversion_factor IS 'How many base units per conversion unit';

COMMENT ON TABLE item_pricing IS 'Flexible pricing per unit and type (purchase/sales)';
COMMENT ON COLUMN item_pricing.pricing_type IS 'Type: purchase (HPP) or sales (retail)';

-- Log results
DO $$
DECLARE
    products_count INT;
    conversions_count INT;
BEGIN
    SELECT COUNT(*) INTO products_count FROM products WHERE item_type IS NOT NULL;
    SELECT COUNT(*) INTO conversions_count FROM unit_conversions;

    RAISE NOTICE 'V023: Updated % products with item_type', products_count;
    RAISE NOTICE 'V023: Migrated % unit conversions to new table', conversions_count;
END $$;
