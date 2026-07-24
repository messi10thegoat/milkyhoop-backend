-- V023 FIX: Items type and pricing
-- Original skipped because of p.base_unit reference in Step 7 backfill
-- This version: drops wrong stub, creates correct schema, skips Step 7 (data backfill)

-- Drop the wrong stub created earlier (item_id/from_unit/to_unit)
DROP TABLE IF EXISTS unit_conversions CASCADE;

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

-- Step 5: Create unit_conversions table (CORRECT schema: product_id/base_unit/conversion_unit)
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

-- Step 7: SKIP - backfill from p.base_unit (column doesn't exist in our schema)
-- Original: INSERT INTO unit_conversions FROM products p WHERE p.wholesale_unit IS NOT NULL

-- Step 8: Backfill sales_price from harga_jual (if column exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='harga_jual') THEN
        UPDATE products SET sales_price = harga_jual WHERE harga_jual IS NOT NULL AND sales_price IS NULL;
    END IF;
END $$;

-- Step 9: Backfill existing products as goods with track_inventory
UPDATE products SET item_type = 'goods', track_inventory = true WHERE item_type IS NULL;

DO $$ BEGIN RAISE NOTICE 'V023 FIX: unit_conversions recreated with correct schema'; END $$;
