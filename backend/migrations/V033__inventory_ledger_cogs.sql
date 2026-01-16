-- ============================================================================
-- V033: Inventory Ledger & COGS Support
-- ============================================================================
-- Purpose: Add inventory ledger for cost tracking and COGS auto-posting
--          on sales invoices
-- Creates: inventory_ledger table, COGS columns on sales_invoice_items
-- ============================================================================

-- ============================================================================
-- 1. INVENTORY LEDGER TABLE
-- ============================================================================
-- Tracks all inventory movements with cost basis for COGS calculation

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
    source_type VARCHAR(30) NOT NULL,    -- BILL, SALES_INVOICE, OPENING_BALANCE, ADJUSTMENT, CREDIT_NOTE, VENDOR_CREDIT
    source_id UUID,
    source_number VARCHAR(50),

    -- Quantities
    quantity_in DECIMAL(15,4) DEFAULT 0,
    quantity_out DECIMAL(15,4) DEFAULT 0,
    quantity_balance DECIMAL(15,4) NOT NULL,  -- Running balance

    -- Costs (stored in smallest currency unit)
    unit_cost BIGINT NOT NULL,               -- Cost per unit for this transaction
    total_cost BIGINT NOT NULL,              -- quantity * unit_cost
    average_cost BIGINT,                     -- Weighted average after this transaction

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
COMMENT ON COLUMN inventory_ledger.movement_type IS 'PURCHASE=inbound from vendor, SALE=outbound to customer, OPENING=initial balance, ADJUSTMENT=stock adj, RETURN=returns';
COMMENT ON COLUMN inventory_ledger.average_cost IS 'Weighted average cost after this movement - used for COGS on sales';

-- ============================================================================
-- 2. ADD COGS COLUMNS TO SALES_INVOICE_ITEMS
-- ============================================================================

ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS unit_cost BIGINT DEFAULT 0;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS total_cost BIGINT DEFAULT 0;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS is_inventory_item BOOLEAN DEFAULT false;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS cost_source VARCHAR(30) DEFAULT NULL;

COMMENT ON COLUMN sales_invoice_items.unit_cost IS 'Unit cost at time of sale (from weighted average)';
COMMENT ON COLUMN sales_invoice_items.total_cost IS 'Total COGS for this line (quantity * unit_cost)';
COMMENT ON COLUMN sales_invoice_items.is_inventory_item IS 'True if this item is tracked in inventory';
COMMENT ON COLUMN sales_invoice_items.cost_source IS 'Source of cost: WEIGHTED_AVG, PURCHASE_PRICE (fallback), MANUAL';

-- ============================================================================
-- 3. ADD COGS TRACKING TO SALES_INVOICES
-- ============================================================================

ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS cogs_journal_id UUID;
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS total_cogs BIGINT DEFAULT 0;
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS cogs_posted_at TIMESTAMPTZ;

COMMENT ON COLUMN sales_invoices.cogs_journal_id IS 'Journal entry ID for COGS posting';
COMMENT ON COLUMN sales_invoices.total_cogs IS 'Total Cost of Goods Sold for this invoice';

-- ============================================================================
-- 4. VERIFY/CREATE REQUIRED ACCOUNTS
-- ============================================================================

-- HPP Barang Dagang (5-10100) - Cost of Goods Sold
INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active, is_system)
SELECT
    t.tenant_id::uuid,
    '5-10100',
    'HPP Barang Dagang',
    'EXPENSE',
    'DEBIT',
    (SELECT id FROM chart_of_accounts c2 WHERE c2.tenant_id = t.tenant_id::uuid AND c2.code = '5-00000' LIMIT 1),
    true,
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE code = '5-10100' AND tenant_id = t.tenant_id::uuid
);

-- Persediaan Barang Dagang (1-10400) - Inventory Asset
INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active, is_system)
SELECT
    t.tenant_id::uuid,
    '1-10400',
    'Persediaan Barang Dagang',
    'ASSET',
    'DEBIT',
    (SELECT id FROM chart_of_accounts c2 WHERE c2.tenant_id = t.tenant_id::uuid AND c2.code = '1-00000' LIMIT 1),
    true,
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE code = '1-10400' AND tenant_id = t.tenant_id::uuid
);

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_inv_ledger_tenant_product ON inventory_ledger(tenant_id, product_id);
CREATE INDEX IF NOT EXISTS idx_inv_ledger_tenant_date ON inventory_ledger(tenant_id, movement_date);
CREATE INDEX IF NOT EXISTS idx_inv_ledger_source ON inventory_ledger(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_inv_ledger_product_date ON inventory_ledger(product_id, movement_date DESC, created_at DESC);

-- For fast weighted average lookup
CREATE INDEX IF NOT EXISTS idx_inv_ledger_avg_cost ON inventory_ledger(tenant_id, product_id, movement_date DESC, created_at DESC)
    WHERE average_cost IS NOT NULL;

-- ============================================================================
-- 6. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE inventory_ledger ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_inventory_ledger ON inventory_ledger;
CREATE POLICY rls_inventory_ledger ON inventory_ledger
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 7. FUNCTIONS
-- ============================================================================

-- Get weighted average cost for a product
CREATE OR REPLACE FUNCTION get_weighted_average_cost(
    p_tenant_id TEXT,
    p_product_id UUID
) RETURNS BIGINT AS $$
DECLARE
    v_avg_cost BIGINT;
    v_fallback_cost BIGINT;
BEGIN
    -- Try to get from inventory ledger (most recent average_cost)
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
    WHERE tenant_id = p_tenant_id::uuid AND id = p_product_id;

    RETURN COALESCE(v_fallback_cost, 0);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_weighted_average_cost IS 'Returns weighted average cost for product, falls back to purchase_price if no ledger history';

-- Calculate new weighted average after a purchase
CREATE OR REPLACE FUNCTION calculate_weighted_average(
    p_tenant_id TEXT,
    p_product_id UUID,
    p_new_quantity DECIMAL,
    p_new_unit_cost BIGINT
) RETURNS BIGINT AS $$
DECLARE
    v_current_balance DECIMAL;
    v_current_avg_cost BIGINT;
    v_new_avg_cost BIGINT;
BEGIN
    -- Get current balance and average cost
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

    -- Calculate new weighted average
    -- Formula: (current_qty * current_avg + new_qty * new_cost) / (current_qty + new_qty)
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
-- 8. TRIGGER FOR UPDATED_AT (if needed)
-- ============================================================================

-- Note: inventory_ledger entries are immutable (no updates), so no updated_at trigger needed

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
