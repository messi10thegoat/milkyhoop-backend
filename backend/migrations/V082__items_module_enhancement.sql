-- ============================================================================
-- V082: Items Module Enhancement
-- ============================================================================
-- Purpose: Add inventory management columns to products table
-- - COGS account linking
-- - Costing method (weighted_average / fifo)
-- - Item code auto-generation
-- - Opening stock tracking
-- - Price levels (multi-pricing)
-- - Status (active/inactive) and soft-delete
-- - Item code sequence table for atomic numbering
-- ============================================================================

-- ============================================================================
-- STEP 1: Add new columns to products table
-- Note: inventory_account_id already exists from V072
-- ============================================================================

ALTER TABLE products
ADD COLUMN IF NOT EXISTS cogs_account_id UUID,
ADD COLUMN IF NOT EXISTS costing_method VARCHAR(20) DEFAULT 'weighted_average',
ADD COLUMN IF NOT EXISTS item_code VARCHAR(50),
ADD COLUMN IF NOT EXISTS opening_stock DECIMAL(15,4) DEFAULT 0,
ADD COLUMN IF NOT EXISTS opening_stock_rate NUMERIC(18,2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS opening_stock_date DATE,
ADD COLUMN IF NOT EXISTS warehouse_id UUID,
ADD COLUMN IF NOT EXISTS price_levels JSONB DEFAULT '[]',
ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active',
ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- ============================================================================
-- STEP 2: Constraints
-- ============================================================================

-- Costing method constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_products_costing_method'
    ) THEN
        ALTER TABLE products ADD CONSTRAINT chk_products_costing_method
            CHECK (costing_method IN ('weighted_average', 'fifo'));
    END IF;
END $$;

-- Status constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_products_status'
    ) THEN
        ALTER TABLE products ADD CONSTRAINT chk_products_status
            CHECK (status IN ('active', 'inactive'));
    END IF;
END $$;

-- ============================================================================
-- STEP 3: Indexes
-- ============================================================================

-- Item code unique per tenant
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_tenant_item_code
    ON products(tenant_id, item_code)
    WHERE item_code IS NOT NULL AND deleted_at IS NULL;

-- Status filter
CREATE INDEX IF NOT EXISTS idx_products_tenant_status
    ON products(tenant_id, status)
    WHERE deleted_at IS NULL;

-- Soft-delete filter (most queries need this)
CREATE INDEX IF NOT EXISTS idx_products_not_deleted
    ON products(tenant_id, id)
    WHERE deleted_at IS NULL;

-- COGS account lookup
CREATE INDEX IF NOT EXISTS idx_products_cogs_account
    ON products(cogs_account_id)
    WHERE cogs_account_id IS NOT NULL;

-- Warehouse lookup
CREATE INDEX IF NOT EXISTS idx_products_warehouse
    ON products(warehouse_id)
    WHERE warehouse_id IS NOT NULL;

-- ============================================================================
-- STEP 4: Item Code Sequences Table
-- Per-tenant atomic counter for BRG-0001, JSA-0001 format
-- ============================================================================

CREATE TABLE IF NOT EXISTS item_code_sequences (
    tenant_id TEXT NOT NULL,
    prefix VARCHAR(10) NOT NULL,
    last_number INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_item_code_seq UNIQUE(tenant_id, prefix)
);

-- RLS for item_code_sequences
ALTER TABLE item_code_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_item_code_sequences ON item_code_sequences;
CREATE POLICY rls_item_code_sequences ON item_code_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- STEP 5: Backfill existing data
-- ============================================================================

-- All existing items should be active
UPDATE products SET status = 'active' WHERE status IS NULL;

-- ============================================================================
-- STEP 6: Comments
-- ============================================================================

COMMENT ON COLUMN products.cogs_account_id IS 'FK to chart_of_accounts - Akun HPP/COGS (5-10100)';
COMMENT ON COLUMN products.costing_method IS 'weighted_average or fifo - cannot change after transactions exist';
COMMENT ON COLUMN products.item_code IS 'Auto-generated: BRG-0001 (goods) or JSA-0001 (service)';
COMMENT ON COLUMN products.opening_stock IS 'Initial stock quantity at setup time';
COMMENT ON COLUMN products.opening_stock_rate IS 'Unit cost for opening stock (Rupiah)';
COMMENT ON COLUMN products.opening_stock_date IS 'Date of opening stock entry';
COMMENT ON COLUMN products.warehouse_id IS 'Default warehouse for this item';
COMMENT ON COLUMN products.price_levels IS 'JSON array of {name, price, min_quantity} for multi-tier pricing';
COMMENT ON COLUMN products.status IS 'active = normal, inactive = hidden from transaction forms';
COMMENT ON COLUMN products.deleted_at IS 'Soft-delete timestamp, NULL = not deleted';

COMMENT ON TABLE item_code_sequences IS 'Atomic counter for item code generation per tenant per prefix';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V082: Items Module Enhancement completed';
    RAISE NOTICE 'Added columns: cogs_account_id, costing_method, item_code, opening_stock*, warehouse_id, price_levels, status, deleted_at';
    RAISE NOTICE 'Created table: item_code_sequences';
    RAISE NOTICE 'Backfilled: status = active for all existing items';
END $$;
