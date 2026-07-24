-- ============================================
-- V078: Create Missing unit_conversions and item_pricing Tables
-- ============================================
-- Purpose: These tables were supposed to be created in V023 but
-- the migration was only partially applied. This migration creates
-- the missing tables needed for item unit conversion functionality.
-- ============================================

-- Step 1: Create unit_conversions table (if not exists)
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

-- Step 2: Create item_pricing table (if not exists)
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

-- Step 3: Migrate existing unit conversion data from products table (if any)
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

-- Step 4: Add comments for documentation
COMMENT ON TABLE unit_conversions IS 'Maps unit conversions for products (e.g., 1 dus = 12 pcs)';
COMMENT ON COLUMN unit_conversions.base_unit IS 'Smallest unit: pcs, botol, lembar';
COMMENT ON COLUMN unit_conversions.conversion_unit IS 'Larger unit: dus, karton, pack';
COMMENT ON COLUMN unit_conversions.conversion_factor IS 'How many base units per conversion unit';

COMMENT ON TABLE item_pricing IS 'Flexible pricing per unit and type (purchase/sales)';
COMMENT ON COLUMN item_pricing.pricing_type IS 'Type: purchase (HPP) or sales (retail)';

-- Log results
DO $$
DECLARE
    conversions_count INT;
    pricing_count INT;
BEGIN
    SELECT COUNT(*) INTO conversions_count FROM unit_conversions;
    SELECT COUNT(*) INTO pricing_count FROM item_pricing;

    RAISE NOTICE 'V078: unit_conversions table ready with % rows', conversions_count;
    RAISE NOTICE 'V078: item_pricing table ready with % rows', pricing_count;
END $$;
