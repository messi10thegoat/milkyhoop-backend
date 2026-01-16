-- ============================================================================
-- V035: Stock Adjustments Module (Penyesuaian Persediaan)
-- ============================================================================
-- Purpose: Track inventory adjustments with proper accounting journal entries
-- Types: increase, decrease, recount, damaged, expired
-- ============================================================================

-- ============================================================================
-- 1. SEED STOCK ADJUSTMENT EXPENSE ACCOUNT
-- ============================================================================

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '5-10200',
    'Penyesuaian Persediaan',
    'EXPENSE',
    'DEBIT',
    '5-10000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '5-10200' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 2. STOCK ADJUSTMENTS TABLE - Header
-- ============================================================================

CREATE TABLE IF NOT EXISTS stock_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identification
    adjustment_number VARCHAR(50) NOT NULL,

    -- Adjustment info
    adjustment_date DATE NOT NULL,
    adjustment_type VARCHAR(20) NOT NULL,

    -- Reference
    reference_no VARCHAR(100),
    notes TEXT,

    -- Storage location (optional)
    storage_location_id UUID,
    storage_location_name VARCHAR(255),

    -- Totals (calculated from items)
    total_value BIGINT NOT NULL DEFAULT 0,
    item_count INT DEFAULT 0,

    -- Status: draft -> posted -> void
    status VARCHAR(20) DEFAULT 'draft',

    -- Accounting
    journal_id UUID,

    -- Status tracking
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    voided_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID NOT NULL,

    CONSTRAINT uq_sa_tenant_number UNIQUE(tenant_id, adjustment_number),
    CONSTRAINT chk_sa_status CHECK (status IN ('draft', 'posted', 'void')),
    CONSTRAINT chk_sa_type CHECK (adjustment_type IN ('increase', 'decrease', 'recount', 'damaged', 'expired'))
);

COMMENT ON TABLE stock_adjustments IS 'Stock Adjustments - Penyesuaian Persediaan dengan Jurnal';
COMMENT ON COLUMN stock_adjustments.adjustment_type IS 'increase=tambah stok, decrease=kurangi stok, recount=opname, damaged=rusak, expired=kadaluarsa';

-- ============================================================================
-- 3. STOCK ADJUSTMENT ITEMS TABLE - Line items
-- ============================================================================

CREATE TABLE IF NOT EXISTS stock_adjustment_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_adjustment_id UUID NOT NULL REFERENCES stock_adjustments(id) ON DELETE CASCADE,

    -- Product reference
    product_id UUID NOT NULL,
    product_code VARCHAR(50),
    product_name VARCHAR(255) NOT NULL,

    -- Quantities
    quantity_before DECIMAL(15,4) NOT NULL DEFAULT 0,
    quantity_adjustment DECIMAL(15,4) NOT NULL,
    quantity_after DECIMAL(15,4) NOT NULL,
    unit VARCHAR(50),

    -- Cost
    unit_cost BIGINT NOT NULL,
    total_value BIGINT NOT NULL,

    -- Adjustment reason per item (optional)
    reason_detail TEXT,

    -- For recount type
    system_quantity DECIMAL(15,4),
    physical_quantity DECIMAL(15,4),

    line_number INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE stock_adjustment_items IS 'Line items for stock adjustments';
COMMENT ON COLUMN stock_adjustment_items.quantity_adjustment IS 'Positive for increase, negative for decrease';
COMMENT ON COLUMN stock_adjustment_items.unit_cost IS 'Weighted average cost at time of adjustment';

-- ============================================================================
-- 4. STOCK ADJUSTMENT SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS stock_adjustment_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INT DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'SA',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_sa_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_sa_tenant_status ON stock_adjustments(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_sa_tenant_date ON stock_adjustments(tenant_id, adjustment_date);
CREATE INDEX IF NOT EXISTS idx_sa_tenant_type ON stock_adjustments(tenant_id, adjustment_type);
CREATE INDEX IF NOT EXISTS idx_sa_number ON stock_adjustments(tenant_id, adjustment_number);
CREATE INDEX IF NOT EXISTS idx_sa_storage ON stock_adjustments(storage_location_id);

CREATE INDEX IF NOT EXISTS idx_sa_items_adj ON stock_adjustment_items(stock_adjustment_id);
CREATE INDEX IF NOT EXISTS idx_sa_items_product ON stock_adjustment_items(product_id);

-- ============================================================================
-- 6. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE stock_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_adjustment_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_adjustment_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_stock_adjustments ON stock_adjustments;
DROP POLICY IF EXISTS rls_stock_adjustment_items ON stock_adjustment_items;
DROP POLICY IF EXISTS rls_stock_adjustment_sequences ON stock_adjustment_sequences;

CREATE POLICY rls_stock_adjustments ON stock_adjustments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_stock_adjustment_items ON stock_adjustment_items
    FOR ALL USING (stock_adjustment_id IN (
        SELECT id FROM stock_adjustments WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

CREATE POLICY rls_stock_adjustment_sequences ON stock_adjustment_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 7. FUNCTIONS
-- ============================================================================

-- Generate adjustment number: SA-YYMM-0001
CREATE OR REPLACE FUNCTION generate_stock_adjustment_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'SA'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_sa_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO stock_adjustment_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = stock_adjustment_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_sa_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_sa_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_stock_adjustment_number IS 'Generates sequential stock adjustment number per tenant per month';

-- ============================================================================
-- 8. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_stock_adjustments_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stock_adjustments_updated_at ON stock_adjustments;
CREATE TRIGGER trg_stock_adjustments_updated_at
    BEFORE UPDATE ON stock_adjustments
    FOR EACH ROW EXECUTE FUNCTION update_stock_adjustments_updated_at();

-- Auto-recalculate totals when items change
CREATE OR REPLACE FUNCTION update_stock_adjustment_totals()
RETURNS TRIGGER AS $$
DECLARE
    v_sa_id UUID;
    v_total BIGINT;
    v_count INT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_sa_id := OLD.stock_adjustment_id;
    ELSE
        v_sa_id := NEW.stock_adjustment_id;
    END IF;

    SELECT COALESCE(SUM(ABS(total_value)), 0), COUNT(*)
    INTO v_total, v_count
    FROM stock_adjustment_items
    WHERE stock_adjustment_id = v_sa_id;

    UPDATE stock_adjustments
    SET total_value = v_total, item_count = v_count, updated_at = NOW()
    WHERE id = v_sa_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_sa_totals ON stock_adjustment_items;
CREATE TRIGGER trg_update_sa_totals
    AFTER INSERT OR UPDATE OR DELETE ON stock_adjustment_items
    FOR EACH ROW EXECUTE FUNCTION update_stock_adjustment_totals();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V035: Stock Adjustments created successfully';
    RAISE NOTICE 'Tables: stock_adjustments, stock_adjustment_items, stock_adjustment_sequences';
    RAISE NOTICE 'New account: 5-10200 Penyesuaian Persediaan';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
