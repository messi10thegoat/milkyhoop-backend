-- ============================================================================
-- V033: Inventory Ledger & COGS Support - FIXED
-- ============================================================================
-- Purpose: Add inventory ledger for cost tracking and COGS auto-posting
-- Adapted to actual database schema
-- ============================================================================

-- ============================================================================
-- 1. INVENTORY LEDGER TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Product reference
    product_id UUID NOT NULL,
    product_code VARCHAR(50),
    product_name VARCHAR(255),

    -- Movement details
    movement_type VARCHAR(30) NOT NULL,  -- PURCHASE, SALE, OPENING, ADJUSTMENT, RETURN
    movement_date DATE NOT NULL,

    -- Source document reference
    source_type VARCHAR(30) NOT NULL,
    source_id UUID,
    source_number VARCHAR(50),

    -- Quantities
    quantity_in DECIMAL(15,4) DEFAULT 0,
    quantity_out DECIMAL(15,4) DEFAULT 0,
    quantity_balance DECIMAL(15,4) NOT NULL,

    -- Costs (stored in smallest currency unit or NUMERIC for precision)
    unit_cost NUMERIC(18,2) NOT NULL,
    total_cost NUMERIC(18,2) NOT NULL,
    average_cost NUMERIC(18,2),

    -- Storage location (optional)
    storage_location_id UUID,

    -- Journal reference
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    notes TEXT
);

COMMENT ON TABLE inventory_ledger IS 'Tracks all inventory movements with cost basis for weighted average COGS calculation';
COMMENT ON COLUMN inventory_ledger.movement_type IS 'PURCHASE=inbound, SALE=outbound, OPENING=initial, ADJUSTMENT=adj, RETURN=returns';
COMMENT ON COLUMN inventory_ledger.average_cost IS 'Weighted average cost after this movement';

-- ============================================================================
-- 2. VERIFY/CREATE REQUIRED ACCOUNTS
-- ============================================================================

-- HPP Barang Dagang (5-10100) - Cost of Goods Sold
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '5-10100',
    'HPP Barang Dagang',
    'EXPENSE',
    'DEBIT',
    '5-00000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '5-10100' AND tenant_id = t.tenant_id
);

-- Persediaan Barang Dagang (1-10400) - Inventory Asset
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '1-10400',
    'Persediaan Barang Dagang',
    'ASSET',
    'DEBIT',
    '1-00000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '1-10400' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 3. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_inv_ledger_tenant_product ON inventory_ledger(tenant_id, product_id);
CREATE INDEX IF NOT EXISTS idx_inv_ledger_tenant_date ON inventory_ledger(tenant_id, movement_date);
CREATE INDEX IF NOT EXISTS idx_inv_ledger_source ON inventory_ledger(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_inv_ledger_product_date ON inventory_ledger(product_id, movement_date DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_inv_ledger_avg_cost ON inventory_ledger(tenant_id, product_id, movement_date DESC, created_at DESC)
    WHERE average_cost IS NOT NULL;

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE inventory_ledger ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_inventory_ledger ON inventory_ledger;
CREATE POLICY rls_inventory_ledger ON inventory_ledger
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 5. FUNCTIONS
-- ============================================================================

-- Get weighted average cost for a product
CREATE OR REPLACE FUNCTION get_weighted_average_cost(
    p_tenant_id TEXT,
    p_product_id UUID
) RETURNS NUMERIC AS $$
DECLARE
    v_avg_cost NUMERIC;
    v_fallback_cost NUMERIC;
BEGIN
    SELECT average_cost INTO v_avg_cost
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id
      AND product_id = p_product_id
      AND average_cost IS NOT NULL
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    IF v_avg_cost IS NOT NULL THEN
        RETURN v_avg_cost;
    END IF;

    -- Fallback to product purchase_price
    SELECT purchase_price INTO v_fallback_cost
    FROM products
    WHERE tenant_id = p_tenant_id AND id = p_product_id;

    RETURN COALESCE(v_fallback_cost, 0);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_weighted_average_cost IS 'Returns weighted average cost for product, falls back to purchase_price';

-- Calculate new weighted average after a purchase
CREATE OR REPLACE FUNCTION calculate_weighted_average(
    p_tenant_id TEXT,
    p_product_id UUID,
    p_new_quantity DECIMAL,
    p_new_unit_cost NUMERIC
) RETURNS NUMERIC AS $$
DECLARE
    v_current_balance DECIMAL;
    v_current_avg_cost NUMERIC;
    v_new_avg_cost NUMERIC;
BEGIN
    SELECT quantity_balance, COALESCE(average_cost, 0)
    INTO v_current_balance, v_current_avg_cost
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id AND product_id = p_product_id
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    IF v_current_balance IS NULL THEN
        v_current_balance := 0;
        v_current_avg_cost := 0;
    END IF;

    IF (v_current_balance + p_new_quantity) > 0 THEN
        v_new_avg_cost := (
            (v_current_balance * v_current_avg_cost) + (p_new_quantity * p_new_unit_cost)
        ) / (v_current_balance + p_new_quantity);
    ELSE
        v_new_avg_cost := p_new_unit_cost;
    END IF;

    RETURN v_new_avg_cost;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION calculate_weighted_average IS 'Calculates new weighted average cost after a purchase';

-- Get current inventory balance for a product
CREATE OR REPLACE FUNCTION get_inventory_balance(
    p_tenant_id TEXT,
    p_product_id UUID
) RETURNS DECIMAL AS $$
DECLARE
    v_balance DECIMAL;
BEGIN
    SELECT quantity_balance INTO v_balance
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id AND product_id = p_product_id
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    RETURN COALESCE(v_balance, 0);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_inventory_balance IS 'Returns current inventory balance for a product from ledger';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
