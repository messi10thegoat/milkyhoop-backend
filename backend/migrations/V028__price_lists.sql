-- V028: Price Lists (Daftar Harga)
-- Creates price lists for multi-tier pricing strategies
-- Supports customer-specific pricing, wholesale pricing, etc.

-- ============================================================================
-- PRICE LISTS TABLE - Header
-- ============================================================================
CREATE TABLE IF NOT EXISTS price_lists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identity
    code VARCHAR(50) NOT NULL,               -- e.g., RETAIL, WHOLESALE, VIP
    name VARCHAR(255) NOT NULL,              -- e.g., Harga Retail, Harga Grosir

    -- Configuration
    price_type VARCHAR(20) DEFAULT 'fixed',  -- fixed, discount_percent, markup_percent
    default_discount DECIMAL(5,2) DEFAULT 0, -- Default discount for this list
    default_markup DECIMAL(5,2) DEFAULT 0,   -- Default markup for this list
    currency VARCHAR(3) DEFAULT 'IDR',

    -- Validity
    start_date DATE,
    end_date DATE,

    -- Priority (lower = higher priority for overlapping dates)
    priority INT DEFAULT 100,

    -- Metadata
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,        -- Default price list for new customers

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    -- Constraints
    CONSTRAINT uq_price_lists_tenant_code UNIQUE(tenant_id, code),
    CONSTRAINT chk_price_type CHECK (price_type IN ('fixed', 'discount_percent', 'markup_percent'))
);

-- ============================================================================
-- PRICE LIST ITEMS TABLE - Line items
-- ============================================================================
CREATE TABLE IF NOT EXISTS price_list_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    price_list_id UUID NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,

    -- Item reference
    item_id UUID NOT NULL,                   -- Reference to items table
    item_code VARCHAR(50),                   -- Denormalized for display

    -- Pricing
    unit VARCHAR(20),                        -- Unit for this price (e.g., pcs, dus)
    price BIGINT NOT NULL,                   -- Price in IDR
    min_quantity DECIMAL(10,2) DEFAULT 1,    -- Minimum qty for this price

    -- Override discount/markup
    discount_percent DECIMAL(5,2),
    markup_percent DECIMAL(5,2),

    -- Validity (item-level override)
    start_date DATE,
    end_date DATE,

    is_active BOOLEAN DEFAULT true,

    -- Constraints
    CONSTRAINT uq_price_list_items UNIQUE(price_list_id, item_id, unit, min_quantity)
);

-- ============================================================================
-- CUSTOMER PRICE LIST ASSIGNMENT
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer_price_lists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    price_list_id UUID NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
    priority INT DEFAULT 100,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_customer_price_list UNIQUE(customer_id, price_list_id)
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_price_lists_tenant ON price_lists(tenant_id);
CREATE INDEX IF NOT EXISTS idx_price_lists_tenant_active ON price_lists(tenant_id, is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_price_lists_dates ON price_lists(tenant_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_price_list_items_list ON price_list_items(price_list_id);
CREATE INDEX IF NOT EXISTS idx_price_list_items_item ON price_list_items(item_id);
CREATE INDEX IF NOT EXISTS idx_customer_price_lists_customer ON customer_price_lists(customer_id);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE price_lists ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_list_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_price_lists ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_price_lists ON price_lists
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_price_list_items ON price_list_items
    FOR ALL USING (price_list_id IN (
        SELECT id FROM price_lists WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_customer_price_lists ON customer_price_lists
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- FUNCTION: Get price for item and customer
-- ============================================================================
CREATE OR REPLACE FUNCTION get_item_price(
    p_tenant_id TEXT,
    p_item_id UUID,
    p_customer_id UUID DEFAULT NULL,
    p_quantity DECIMAL DEFAULT 1,
    p_unit VARCHAR DEFAULT NULL
) RETURNS BIGINT AS $$
DECLARE
    v_price BIGINT;
BEGIN
    -- First try customer-specific price list
    IF p_customer_id IS NOT NULL THEN
        SELECT pli.price INTO v_price
        FROM price_list_items pli
        JOIN price_lists pl ON pli.price_list_id = pl.id
        JOIN customer_price_lists cpl ON pl.id = cpl.price_list_id
        WHERE pl.tenant_id = p_tenant_id
          AND cpl.customer_id = p_customer_id
          AND cpl.is_active = true
          AND pli.item_id = p_item_id
          AND pli.is_active = true
          AND pl.is_active = true
          AND (pli.unit = p_unit OR pli.unit IS NULL OR p_unit IS NULL)
          AND pli.min_quantity <= p_quantity
          AND (pl.start_date IS NULL OR pl.start_date <= CURRENT_DATE)
          AND (pl.end_date IS NULL OR pl.end_date >= CURRENT_DATE)
        ORDER BY cpl.priority ASC, pli.min_quantity DESC
        LIMIT 1;

        IF v_price IS NOT NULL THEN
            RETURN v_price;
        END IF;
    END IF;

    -- Fall back to default price list
    SELECT pli.price INTO v_price
    FROM price_list_items pli
    JOIN price_lists pl ON pli.price_list_id = pl.id
    WHERE pl.tenant_id = p_tenant_id
      AND pl.is_default = true
      AND pl.is_active = true
      AND pli.item_id = p_item_id
      AND pli.is_active = true
      AND (pli.unit = p_unit OR pli.unit IS NULL OR p_unit IS NULL)
      AND pli.min_quantity <= p_quantity
      AND (pl.start_date IS NULL OR pl.start_date <= CURRENT_DATE)
      AND (pl.end_date IS NULL OR pl.end_date >= CURRENT_DATE)
    ORDER BY pli.min_quantity DESC
    LIMIT 1;

    RETURN v_price;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_price_lists_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_price_lists_updated_at
    BEFORE UPDATE ON price_lists
    FOR EACH ROW EXECUTE FUNCTION trigger_price_lists_updated_at();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE price_lists IS 'Daftar harga untuk strategi pricing multi-tier';
COMMENT ON COLUMN price_lists.price_type IS 'Tipe pricing: fixed (harga tetap), discount_percent, markup_percent';
COMMENT ON COLUMN price_lists.priority IS 'Prioritas untuk price list yang tumpang tindih (lower = higher priority)';
COMMENT ON FUNCTION get_item_price IS 'Mendapatkan harga item berdasarkan customer, quantity, dan unit';
