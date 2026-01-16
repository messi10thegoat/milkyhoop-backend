-- =============================================
-- V066: Recipe Management (Resep & Menu)
-- Purpose: Manage food recipes with ingredients and portions for F&B
-- =============================================

-- ============================================================================
-- 1. RECIPES
-- ============================================================================

CREATE TABLE IF NOT EXISTS recipes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Menu item this produces
    product_id UUID NOT NULL REFERENCES products(id),

    -- Recipe info
    recipe_code VARCHAR(50) NOT NULL,
    recipe_name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Category
    category VARCHAR(50), -- appetizer, main_course, dessert, beverage, etc
    cuisine_type VARCHAR(50), -- indonesian, western, chinese, etc

    -- Yield
    yield_quantity DECIMAL(15,4) DEFAULT 1,
    yield_unit VARCHAR(50) DEFAULT 'portion',

    -- Timing
    prep_time_minutes INTEGER,
    cook_time_minutes INTEGER,
    total_time_minutes INTEGER,

    -- Costing
    ingredient_cost BIGINT DEFAULT 0,
    labor_cost_per_portion BIGINT DEFAULT 0,
    overhead_per_portion BIGINT DEFAULT 0,
    total_cost_per_portion BIGINT DEFAULT 0,

    -- Pricing
    suggested_price BIGINT,
    target_food_cost_percent DECIMAL(5,2) DEFAULT 30,
    actual_food_cost_percent DECIMAL(5,2),

    -- Allergens & dietary
    allergens TEXT[],
    dietary_tags TEXT[],

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, active, discontinued

    -- Version
    version INTEGER DEFAULT 1,

    -- Image
    image_url TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_recipes UNIQUE(tenant_id, recipe_code),
    CONSTRAINT chk_recipe_status CHECK (status IN ('draft', 'active', 'discontinued'))
);

-- ============================================================================
-- 2. RECIPE INGREDIENTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,

    -- Ingredient
    ingredient_product_id UUID NOT NULL REFERENCES products(id),

    -- Quantity
    quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(50) NOT NULL, -- gram, ml, pcs, tbsp, tsp, cup, etc

    -- For scaling
    base_quantity DECIMAL(15,4),

    -- Wastage
    wastage_percent DECIMAL(5,2) DEFAULT 0,

    -- Preparation
    preparation_note TEXT, -- "diced", "minced", "sliced thin"

    -- Costing
    unit_cost BIGINT DEFAULT 0,
    extended_cost BIGINT DEFAULT 0,

    -- Substitute
    is_optional BOOLEAN DEFAULT false,
    substitute_ingredient_id UUID REFERENCES products(id),

    -- Order
    sequence_order INTEGER DEFAULT 0
);

-- ============================================================================
-- 3. RECIPE INSTRUCTIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS recipe_instructions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,

    step_number INTEGER NOT NULL,
    instruction TEXT NOT NULL,

    -- Timing
    duration_minutes INTEGER,

    -- Equipment
    equipment_needed TEXT,

    -- Tips
    tips TEXT,

    -- Image
    image_url TEXT,

    CONSTRAINT uq_recipe_instructions UNIQUE(recipe_id, step_number)
);

-- ============================================================================
-- 4. RECIPE MODIFIERS (Add-ons, Variations)
-- ============================================================================

CREATE TABLE IF NOT EXISTS recipe_modifiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,

    modifier_name VARCHAR(100) NOT NULL,
    modifier_type VARCHAR(50), -- size, spice_level, add_on, removal

    -- Pricing
    price_adjustment BIGINT DEFAULT 0, -- can be positive or negative

    -- Ingredient adjustments
    ingredient_adjustments JSONB, -- [{product_id, quantity_change}]

    is_default BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,

    display_order INTEGER DEFAULT 0
);

-- ============================================================================
-- 5. MENU CATEGORIES
-- ============================================================================

CREATE TABLE IF NOT EXISTS menu_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    name VARCHAR(100) NOT NULL,
    description TEXT,

    parent_category_id UUID REFERENCES menu_categories(id),
    display_order INTEGER DEFAULT 0,

    -- Availability
    available_from TIME,
    available_until TIME,
    available_days INTEGER[], -- 0=Sunday, 6=Saturday

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_menu_categories UNIQUE(tenant_id, name)
);

-- ============================================================================
-- 6. MENU ITEMS (Link Recipe to Menu)
-- ============================================================================

CREATE TABLE IF NOT EXISTS menu_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    recipe_id UUID NOT NULL REFERENCES recipes(id),
    category_id UUID REFERENCES menu_categories(id),

    -- Display
    display_name VARCHAR(255),
    short_description TEXT,

    -- Pricing
    base_price BIGINT NOT NULL,
    discount_price BIGINT,
    discount_start_date DATE,
    discount_end_date DATE,

    -- Availability
    is_available BOOLEAN DEFAULT true,
    available_quantity INTEGER, -- NULL = unlimited
    max_per_order INTEGER,

    -- Featured
    is_featured BOOLEAN DEFAULT false,
    is_new BOOLEAN DEFAULT false,

    -- Image
    image_url TEXT,
    thumbnail_url TEXT,

    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE recipes ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_ingredients ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_instructions ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_modifiers ENABLE ROW LEVEL SECURITY;
ALTER TABLE menu_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE menu_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_recipes ON recipes;
CREATE POLICY rls_recipes ON recipes
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_recipe_ingredients ON recipe_ingredients;
CREATE POLICY rls_recipe_ingredients ON recipe_ingredients
    USING (recipe_id IN (SELECT id FROM recipes WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_recipe_instructions ON recipe_instructions;
CREATE POLICY rls_recipe_instructions ON recipe_instructions
    USING (recipe_id IN (SELECT id FROM recipes WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_recipe_modifiers ON recipe_modifiers;
CREATE POLICY rls_recipe_modifiers ON recipe_modifiers
    USING (recipe_id IN (SELECT id FROM recipes WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_menu_categories ON menu_categories;
CREATE POLICY rls_menu_categories ON menu_categories
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_menu_items ON menu_items;
CREATE POLICY rls_menu_items ON menu_items
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_recipes_tenant ON recipes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_recipes_product ON recipes(product_id);
CREATE INDEX IF NOT EXISTS idx_recipes_status ON recipes(tenant_id, status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe ON recipe_ingredients(recipe_id);
CREATE INDEX IF NOT EXISTS idx_menu_items_category ON menu_items(category_id);
CREATE INDEX IF NOT EXISTS idx_menu_items_recipe ON menu_items(recipe_id);

-- ============================================================================
-- FUNCTION: Calculate Recipe Cost
-- ============================================================================

CREATE OR REPLACE FUNCTION calculate_recipe_cost(p_recipe_id UUID)
RETURNS BIGINT AS $$
DECLARE
    v_ingredient_cost BIGINT;
    v_labor_cost BIGINT;
    v_overhead BIGINT;
BEGIN
    -- Sum ingredient costs
    SELECT COALESCE(SUM(extended_cost), 0)
    INTO v_ingredient_cost
    FROM recipe_ingredients
    WHERE recipe_id = p_recipe_id;

    -- Get labor and overhead from recipe
    SELECT labor_cost_per_portion, overhead_per_portion
    INTO v_labor_cost, v_overhead
    FROM recipes
    WHERE id = p_recipe_id;

    -- Update recipe
    UPDATE recipes
    SET ingredient_cost = v_ingredient_cost,
        total_cost_per_portion = v_ingredient_cost + COALESCE(v_labor_cost, 0) + COALESCE(v_overhead, 0),
        actual_food_cost_percent = CASE
            WHEN suggested_price > 0 THEN ROUND((v_ingredient_cost::DECIMAL / suggested_price) * 100, 2)
            ELSE NULL
        END,
        updated_at = NOW()
    WHERE id = p_recipe_id;

    RETURN v_ingredient_cost + COALESCE(v_labor_cost, 0) + COALESCE(v_overhead, 0);
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- NOTE: No direct journal entries - uses production order or sales
-- ============================================================================
