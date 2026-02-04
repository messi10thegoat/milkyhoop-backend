-- ============================================================================
-- V090: Items Advanced Features
-- ============================================================================
-- Purpose: Add 6 new features to Items module
-- 1. Quantity Pricing (tiered pricing based on quantity)
-- 2. Drop Ship & Special Order flags
-- 3. Non-Inventory item type support
-- 4. Lot/Expiry Tracking flags (map to existing batch tracking)
-- 5. Bin Management for warehouse locations
-- 6. Matrix Items (Parent-Variant relationship)
-- ============================================================================

-- ============================================================================
-- 1. QUANTITY PRICING
-- Enables tiered pricing based on quantity purchased/sold
-- Example: [{min_qty: 1, max_qty: 10, price: 10000}, {min_qty: 11, max_qty: 50, price: 9000}]
-- ============================================================================

ALTER TABLE products ADD COLUMN IF NOT EXISTS quantity_pricing_enabled BOOLEAN DEFAULT false;
ALTER TABLE products ADD COLUMN IF NOT EXISTS quantity_pricing JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN products.quantity_pricing_enabled IS 'Enable tiered pricing based on quantity';
COMMENT ON COLUMN products.quantity_pricing IS 'Array of {min_qty, max_qty, price, unit?} for quantity-based pricing tiers';

-- Index for items with quantity pricing enabled
CREATE INDEX IF NOT EXISTS idx_products_quantity_pricing
ON products (tenant_id, id)
WHERE quantity_pricing_enabled = true AND deleted_at IS NULL;

-- ============================================================================
-- 2. DROP SHIP & SPECIAL ORDER FLAGS
-- Drop ship: item ships directly from vendor to customer
-- Special order: item is ordered specifically for customer (not stocked)
-- ============================================================================

ALTER TABLE products ADD COLUMN IF NOT EXISTS is_drop_ship BOOLEAN DEFAULT false;
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_special_order BOOLEAN DEFAULT false;

COMMENT ON COLUMN products.is_drop_ship IS 'Item ships directly from vendor to customer (no warehouse handling)';
COMMENT ON COLUMN products.is_special_order IS 'Item is ordered specifically for customer on demand (not stocked)';

-- Indexes for filtering drop ship and special order items
CREATE INDEX IF NOT EXISTS idx_products_drop_ship
ON products (tenant_id, id)
WHERE is_drop_ship = true AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_products_special_order
ON products (tenant_id, id)
WHERE is_special_order = true AND deleted_at IS NULL;

-- ============================================================================
-- 3. NON-INVENTORY ITEM TYPE
-- Extends item_type enum to include 'non_inventory'
-- Non-inventory items: not tracked in stock but appear in transactions
-- Examples: miscellaneous charges, handling fees, other items
-- ============================================================================

-- Drop existing constraint (safe if not exists)
ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_products_item_type;

-- Add new constraint with non_inventory option
ALTER TABLE products ADD CONSTRAINT chk_products_item_type
    CHECK (item_type IN ('goods', 'service', 'non_inventory'));

COMMENT ON COLUMN products.item_type IS 'Item type: goods (physical with inventory), service (intangible), non_inventory (physical but not tracked)';

-- ============================================================================
-- 4. LOT/EXPIRY TRACKING FLAGS
-- These fields map to existing batch tracking from V047
-- Adding explicit flags for UI clarity and form control
-- ============================================================================

-- track_batches (lot numbers) already exists from V047
-- track_expiry already exists from V047
-- Adding an alias field for UI clarity: track_lot_numbers

ALTER TABLE products ADD COLUMN IF NOT EXISTS track_lot_numbers BOOLEAN 
    GENERATED ALWAYS AS (track_batches) STORED;

-- Note: track_serial already exists from V048

COMMENT ON COLUMN products.track_lot_numbers IS 'Alias for track_batches - enables lot/batch number tracking';

-- ============================================================================
-- 5. BIN MANAGEMENT
-- Warehouse bins are sub-locations within a warehouse
-- Enables precise location tracking: Warehouse > Bin > Item
-- ============================================================================

CREATE TABLE IF NOT EXISTS warehouse_bins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    warehouse_id UUID NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    
    -- Bin identification
    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    
    -- Location within warehouse
    aisle VARCHAR(20),       -- e.g., A, B, C
    rack VARCHAR(20),        -- e.g., 1, 2, 3
    shelf VARCHAR(20),       -- e.g., 1A, 1B, 1C
    position VARCHAR(20),    -- e.g., Front, Back
    
    -- Bin type/purpose
    bin_type VARCHAR(50) DEFAULT 'storage', -- storage, receiving, shipping, returns, quarantine
    
    -- Capacity (optional)
    max_weight DECIMAL(10,2),    -- max weight in kg
    max_volume DECIMAL(10,2),    -- max volume in cubic meters
    max_items INT,               -- max number of items
    
    -- Status
    is_active BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false, -- default bin for this warehouse
    
    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    
    CONSTRAINT uq_warehouse_bin_code UNIQUE(tenant_id, warehouse_id, code)
);

-- Bin type constraint
ALTER TABLE warehouse_bins ADD CONSTRAINT IF NOT EXISTS chk_bin_type
    CHECK (bin_type IN ('storage', 'receiving', 'shipping', 'returns', 'quarantine', 'picking'));

-- Bin stock tracking table
CREATE TABLE IF NOT EXISTS bin_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    bin_id UUID NOT NULL REFERENCES warehouse_bins(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    
    -- Stock quantities
    quantity DECIMAL(15,4) DEFAULT 0,
    reserved_quantity DECIMAL(15,4) DEFAULT 0,
    available_quantity DECIMAL(15,4) GENERATED ALWAYS AS (quantity - reserved_quantity) STORED,
    
    -- Batch/Serial reference (optional)
    batch_id UUID REFERENCES item_batches(id),
    
    -- Last activity
    last_count_date TIMESTAMPTZ,
    last_movement_date TIMESTAMPTZ,
    
    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT uq_bin_item_batch UNIQUE(tenant_id, bin_id, item_id, COALESCE(batch_id, '00000000-0000-0000-0000-000000000000'::uuid)),
    CONSTRAINT chk_bin_stock_qty CHECK (quantity >= 0),
    CONSTRAINT chk_bin_stock_reserved CHECK (reserved_quantity >= 0)
);

-- Add default bin reference to products
ALTER TABLE products ADD COLUMN IF NOT EXISTS default_bin_id UUID REFERENCES warehouse_bins(id);

COMMENT ON TABLE warehouse_bins IS 'Sub-locations within warehouses for precise inventory placement';
COMMENT ON COLUMN warehouse_bins.bin_type IS 'Purpose: storage, receiving, shipping, returns, quarantine, picking';
COMMENT ON TABLE bin_stock IS 'Stock levels per bin per item (optionally per batch)';
COMMENT ON COLUMN products.default_bin_id IS 'Default bin location for this item';

-- RLS for warehouse_bins
ALTER TABLE warehouse_bins ENABLE ROW LEVEL SECURITY;
ALTER TABLE bin_stock ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_warehouse_bins ON warehouse_bins;
DROP POLICY IF EXISTS rls_bin_stock ON bin_stock;

CREATE POLICY rls_warehouse_bins ON warehouse_bins
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bin_stock ON bin_stock
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- Indexes for warehouse_bins
CREATE INDEX IF NOT EXISTS idx_wb_tenant ON warehouse_bins(tenant_id);
CREATE INDEX IF NOT EXISTS idx_wb_warehouse ON warehouse_bins(warehouse_id);
CREATE INDEX IF NOT EXISTS idx_wb_code ON warehouse_bins(tenant_id, warehouse_id, code);
CREATE INDEX IF NOT EXISTS idx_wb_type ON warehouse_bins(tenant_id, bin_type) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_wb_default ON warehouse_bins(tenant_id, warehouse_id, is_default) WHERE is_default = true;

-- Indexes for bin_stock
CREATE INDEX IF NOT EXISTS idx_bs_bin ON bin_stock(bin_id);
CREATE INDEX IF NOT EXISTS idx_bs_item ON bin_stock(item_id);
CREATE INDEX IF NOT EXISTS idx_bs_batch ON bin_stock(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bs_available ON bin_stock(tenant_id, bin_id, item_id) WHERE quantity > 0;

-- ============================================================================
-- 6. MATRIX ITEMS (PARENT-VARIANT)
-- Matrix items are products with variations (size, color, etc.)
-- Parent item holds shared info, variants are actual sellable items
-- ============================================================================

ALTER TABLE products ADD COLUMN IF NOT EXISTS is_matrix_parent BOOLEAN DEFAULT false;
ALTER TABLE products ADD COLUMN IF NOT EXISTS matrix_parent_id UUID REFERENCES products(id);
ALTER TABLE products ADD COLUMN IF NOT EXISTS matrix_attributes JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN products.is_matrix_parent IS 'True if this is a matrix parent (has variants)';
COMMENT ON COLUMN products.matrix_parent_id IS 'Reference to parent item if this is a variant';
COMMENT ON COLUMN products.matrix_attributes IS 'Variant attributes: {size: L, color: Red, ...}';

-- Indexes for matrix items
CREATE INDEX IF NOT EXISTS idx_products_matrix_parent
ON products (tenant_id, id)
WHERE is_matrix_parent = true AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_products_matrix_variants
ON products (tenant_id, matrix_parent_id)
WHERE matrix_parent_id IS NOT NULL AND deleted_at IS NULL;

-- GIN index for searching within matrix_attributes
CREATE INDEX IF NOT EXISTS idx_products_matrix_attrs
ON products USING GIN (matrix_attributes)
WHERE matrix_parent_id IS NOT NULL;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at for warehouse_bins
CREATE OR REPLACE FUNCTION update_warehouse_bins_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_warehouse_bins_updated_at ON warehouse_bins;
CREATE TRIGGER trg_warehouse_bins_updated_at
    BEFORE UPDATE ON warehouse_bins
    FOR EACH ROW EXECUTE FUNCTION update_warehouse_bins_updated_at();

DROP TRIGGER IF EXISTS trg_bin_stock_updated_at ON bin_stock;
CREATE TRIGGER trg_bin_stock_updated_at
    BEFORE UPDATE ON bin_stock
    FOR EACH ROW EXECUTE FUNCTION update_warehouse_bins_updated_at();

-- Ensure only one default bin per warehouse
CREATE OR REPLACE FUNCTION ensure_single_default_bin()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_default = true THEN
        UPDATE warehouse_bins
        SET is_default = false
        WHERE tenant_id = NEW.tenant_id 
          AND warehouse_id = NEW.warehouse_id 
          AND id != NEW.id 
          AND is_default = true;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_single_default_bin ON warehouse_bins;
CREATE TRIGGER trg_single_default_bin
    BEFORE INSERT OR UPDATE ON warehouse_bins
    FOR EACH ROW
    WHEN (NEW.is_default = true)
    EXECUTE FUNCTION ensure_single_default_bin();

-- Prevent circular matrix references
CREATE OR REPLACE FUNCTION prevent_circular_matrix_reference()
RETURNS TRIGGER AS $$
DECLARE
    v_parent_has_parent UUID;
BEGIN
    -- Don't allow matrix_parent_id if is_matrix_parent is true
    IF NEW.is_matrix_parent = true AND NEW.matrix_parent_id IS NOT NULL THEN
        RAISE EXCEPTION 'Matrix parent items cannot have a parent reference';
    END IF;
    
    -- Don't allow self-reference
    IF NEW.matrix_parent_id = NEW.id THEN
        RAISE EXCEPTION 'Item cannot be its own parent';
    END IF;
    
    -- Check if parent is itself a variant (prevent multi-level nesting)
    IF NEW.matrix_parent_id IS NOT NULL THEN
        SELECT matrix_parent_id INTO v_parent_has_parent
        FROM products
        WHERE id = NEW.matrix_parent_id;
        
        IF v_parent_has_parent IS NOT NULL THEN
            RAISE EXCEPTION 'Cannot create nested variants (parent is already a variant)';
        END IF;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_circular_matrix ON products;
CREATE TRIGGER trg_prevent_circular_matrix
    BEFORE INSERT OR UPDATE ON products
    FOR EACH ROW
    WHEN (NEW.is_matrix_parent = true OR NEW.matrix_parent_id IS NOT NULL)
    EXECUTE FUNCTION prevent_circular_matrix_reference();

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Get quantity-based price for an item
CREATE OR REPLACE FUNCTION get_quantity_price(
    p_tenant_id TEXT,
    p_item_id UUID,
    p_quantity DECIMAL,
    p_price_type VARCHAR DEFAULT 'sales' -- 'sales' or 'purchase'
) RETURNS DECIMAL AS $$
DECLARE
    v_item RECORD;
    v_tier JSONB;
    v_result DECIMAL;
BEGIN
    -- Get item with pricing info
    SELECT * INTO v_item
    FROM products
    WHERE tenant_id = p_tenant_id AND id = p_item_id;
    
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    
    -- If quantity pricing not enabled, return base price
    IF NOT v_item.quantity_pricing_enabled OR v_item.quantity_pricing IS NULL THEN
        IF p_price_type = 'sales' THEN
            RETURN v_item.sales_price;
        ELSE
            RETURN v_item.purchase_price;
        END IF;
    END IF;
    
    -- Find matching tier
    FOR v_tier IN SELECT * FROM jsonb_array_elements(v_item.quantity_pricing)
    LOOP
        IF p_quantity >= (v_tier->>'min_qty')::DECIMAL 
           AND (v_tier->>'max_qty' IS NULL OR p_quantity <= (v_tier->>'max_qty')::DECIMAL) THEN
            RETURN (v_tier->>'price')::DECIMAL;
        END IF;
    END LOOP;
    
    -- No tier found, return base price
    IF p_price_type = 'sales' THEN
        RETURN v_item.sales_price;
    ELSE
        RETURN v_item.purchase_price;
    END IF;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_quantity_price IS 'Returns the appropriate price for an item based on quantity tiers';

-- Get available bins for an item in a warehouse
CREATE OR REPLACE FUNCTION get_item_bins(
    p_tenant_id TEXT,
    p_item_id UUID,
    p_warehouse_id UUID DEFAULT NULL
) RETURNS TABLE(
    bin_id UUID,
    bin_code VARCHAR,
    bin_name VARCHAR,
    warehouse_id UUID,
    warehouse_name VARCHAR,
    quantity DECIMAL,
    available_quantity DECIMAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        wb.id as bin_id,
        wb.code as bin_code,
        wb.name as bin_name,
        wb.warehouse_id,
        w.name as warehouse_name,
        bs.quantity,
        bs.available_quantity
    FROM bin_stock bs
    JOIN warehouse_bins wb ON bs.bin_id = wb.id
    JOIN warehouses w ON wb.warehouse_id = w.id
    WHERE bs.tenant_id = p_tenant_id
      AND bs.item_id = p_item_id
      AND (p_warehouse_id IS NULL OR wb.warehouse_id = p_warehouse_id)
      AND wb.is_active = true
      AND bs.quantity > 0
    ORDER BY w.name, wb.code;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_item_bins IS 'Returns bins containing stock for an item';

-- Get variants for a matrix parent
CREATE OR REPLACE FUNCTION get_matrix_variants(
    p_tenant_id TEXT,
    p_parent_id UUID
) RETURNS TABLE(
    variant_id UUID,
    variant_name VARCHAR,
    attributes JSONB,
    sales_price DECIMAL,
    purchase_price DECIMAL,
    current_stock DECIMAL,
    status VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        p.id as variant_id,
        p.nama_produk::VARCHAR as variant_name,
        p.matrix_attributes as attributes,
        p.sales_price,
        p.purchase_price,
        COALESCE(p.stock::DECIMAL, 0) as current_stock,
        p.status
    FROM products p
    WHERE p.tenant_id = p_tenant_id
      AND p.matrix_parent_id = p_parent_id
      AND p.deleted_at IS NULL
    ORDER BY p.nama_produk;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_matrix_variants IS 'Returns all variants for a matrix parent item';

-- ============================================================================
-- BACKFILL & CLEANUP
-- ============================================================================

-- Ensure all existing items have default values for new fields
UPDATE products SET
    quantity_pricing_enabled = COALESCE(quantity_pricing_enabled, false),
    quantity_pricing = COALESCE(quantity_pricing, '[]'::jsonb),
    is_drop_ship = COALESCE(is_drop_ship, false),
    is_special_order = COALESCE(is_special_order, false),
    is_matrix_parent = COALESCE(is_matrix_parent, false),
    matrix_attributes = COALESCE(matrix_attributes, '{}'::jsonb)
WHERE deleted_at IS NULL;

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V090: Items Advanced Features completed';
    RAISE NOTICE 'Added: quantity_pricing_enabled, quantity_pricing (JSONB)';
    RAISE NOTICE 'Added: is_drop_ship, is_special_order flags';
    RAISE NOTICE 'Updated: item_type constraint to include non_inventory';
    RAISE NOTICE 'Added: track_lot_numbers (alias for track_batches)';
    RAISE NOTICE 'Created: warehouse_bins, bin_stock tables';
    RAISE NOTICE 'Added: is_matrix_parent, matrix_parent_id, matrix_attributes';
    RAISE NOTICE 'Created functions: get_quantity_price, get_item_bins, get_matrix_variants';
END $$;
