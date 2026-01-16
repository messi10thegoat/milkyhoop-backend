-- ============================================================================
-- V046: Recurring Invoices (Faktur Berulang)
-- ============================================================================
-- Purpose: Template-based automatic invoice generation on schedule
-- Tables: recurring_invoices, recurring_invoice_items
-- Extends: sales_invoices with recurring_invoice_id
-- ============================================================================

-- ============================================================================
-- 1. RECURRING INVOICES TABLE - Template with schedule
-- ============================================================================

CREATE TABLE IF NOT EXISTS recurring_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Template info
    template_name VARCHAR(100) NOT NULL,
    template_code VARCHAR(50),

    -- Customer
    customer_id VARCHAR(255) NOT NULL REFERENCES customers(id),
    customer_name VARCHAR(255),

    -- Warehouse
    warehouse_id UUID REFERENCES warehouses(id),

    -- Schedule
    frequency VARCHAR(20) NOT NULL, -- daily, weekly, monthly, quarterly, yearly
    interval_count INTEGER DEFAULT 1, -- every X frequency (e.g., every 2 months)
    day_of_month INTEGER, -- for monthly: 1-28 (day to generate)
    day_of_week INTEGER, -- for weekly: 0=Sun, 1=Mon, ..., 6=Sat

    -- Dates
    start_date DATE NOT NULL,
    end_date DATE, -- NULL = indefinite
    next_invoice_date DATE NOT NULL,
    last_invoice_date DATE,

    -- Invoice defaults
    due_days INTEGER DEFAULT 30,
    payment_terms TEXT,

    -- Amounts (template)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL DEFAULT 0,

    -- Settings
    auto_send BOOLEAN DEFAULT false, -- Auto-email on generate
    auto_post BOOLEAN DEFAULT false, -- Auto-post to accounting

    -- Status
    status VARCHAR(20) DEFAULT 'active', -- active, paused, completed, cancelled

    -- Stats
    invoices_generated INTEGER DEFAULT 0,
    total_invoiced BIGINT DEFAULT 0,

    -- Notes
    invoice_notes TEXT,
    internal_notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    paused_at TIMESTAMPTZ,
    paused_by UUID,
    pause_reason TEXT,

    CONSTRAINT uq_ri_template_code UNIQUE(tenant_id, template_code),
    CONSTRAINT chk_ri_frequency CHECK (frequency IN ('daily', 'weekly', 'monthly', 'quarterly', 'yearly')),
    CONSTRAINT chk_ri_status CHECK (status IN ('active', 'paused', 'completed', 'cancelled')),
    CONSTRAINT chk_ri_interval CHECK (interval_count >= 1),
    CONSTRAINT chk_ri_day_month CHECK (day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 28)),
    CONSTRAINT chk_ri_day_week CHECK (day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6))
);

COMMENT ON TABLE recurring_invoices IS 'Recurring invoice templates with generation schedule';
COMMENT ON COLUMN recurring_invoices.frequency IS 'daily, weekly, monthly, quarterly, yearly';
COMMENT ON COLUMN recurring_invoices.interval_count IS 'Every X frequency (e.g., every 2 months = monthly + 2)';
COMMENT ON COLUMN recurring_invoices.day_of_month IS 'For monthly: day to generate (1-28)';
COMMENT ON COLUMN recurring_invoices.day_of_week IS 'For weekly: 0=Sun to 6=Sat';

-- ============================================================================
-- 2. RECURRING INVOICE ITEMS TABLE - Line items template
-- ============================================================================

CREATE TABLE IF NOT EXISTS recurring_invoice_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recurring_invoice_id UUID NOT NULL REFERENCES recurring_invoices(id) ON DELETE CASCADE,

    -- Product (optional - can be description only)
    item_id UUID,
    item_code VARCHAR(50),
    item_name VARCHAR(255),
    description TEXT NOT NULL,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL DEFAULT 1,
    unit VARCHAR(50),

    -- Pricing
    unit_price BIGINT NOT NULL,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,

    -- Tax
    tax_id UUID,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,

    -- Line total
    subtotal BIGINT NOT NULL,
    line_total BIGINT NOT NULL,

    line_number INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_rii_qty CHECK (quantity > 0),
    CONSTRAINT chk_rii_price CHECK (unit_price >= 0)
);

COMMENT ON TABLE recurring_invoice_items IS 'Line items template for recurring invoices';

-- ============================================================================
-- 3. EXTEND SALES_INVOICES TABLE
-- ============================================================================

ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS recurring_invoice_id UUID REFERENCES recurring_invoices(id);
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT false;

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE recurring_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE recurring_invoice_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_recurring_invoices ON recurring_invoices;
DROP POLICY IF EXISTS rls_recurring_invoice_items ON recurring_invoice_items;

CREATE POLICY rls_recurring_invoices ON recurring_invoices
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_recurring_invoice_items ON recurring_invoice_items
    FOR ALL USING (recurring_invoice_id IN (
        SELECT id FROM recurring_invoices WHERE tenant_id = current_setting('app.tenant_id', true)
    ));

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_ri_tenant_status ON recurring_invoices(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ri_next_date ON recurring_invoices(tenant_id, next_invoice_date) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_ri_customer ON recurring_invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_ri_template_code ON recurring_invoices(tenant_id, template_code);

CREATE INDEX IF NOT EXISTS idx_rii_recurring ON recurring_invoice_items(recurring_invoice_id);

-- Index for finding generated invoices
CREATE INDEX IF NOT EXISTS idx_si_recurring ON sales_invoices(recurring_invoice_id) WHERE recurring_invoice_id IS NOT NULL;

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

-- Calculate next invoice date based on frequency
CREATE OR REPLACE FUNCTION calculate_next_invoice_date(
    p_current_date DATE,
    p_frequency VARCHAR,
    p_interval INT DEFAULT 1,
    p_day_of_month INT DEFAULT NULL,
    p_day_of_week INT DEFAULT NULL
) RETURNS DATE AS $$
DECLARE
    v_next DATE;
BEGIN
    CASE p_frequency
        WHEN 'daily' THEN
            v_next := p_current_date + (p_interval || ' days')::INTERVAL;

        WHEN 'weekly' THEN
            v_next := p_current_date + (p_interval || ' weeks')::INTERVAL;
            -- Adjust to specific day of week if set
            IF p_day_of_week IS NOT NULL THEN
                v_next := v_next + ((p_day_of_week - EXTRACT(DOW FROM v_next))::INT || ' days')::INTERVAL;
                IF v_next <= p_current_date THEN
                    v_next := v_next + '7 days'::INTERVAL;
                END IF;
            END IF;

        WHEN 'monthly' THEN
            v_next := p_current_date + (p_interval || ' months')::INTERVAL;
            -- Adjust to specific day of month if set
            IF p_day_of_month IS NOT NULL THEN
                v_next := DATE_TRUNC('month', v_next) + ((p_day_of_month - 1) || ' days')::INTERVAL;
            END IF;

        WHEN 'quarterly' THEN
            v_next := p_current_date + ((p_interval * 3) || ' months')::INTERVAL;
            IF p_day_of_month IS NOT NULL THEN
                v_next := DATE_TRUNC('month', v_next) + ((p_day_of_month - 1) || ' days')::INTERVAL;
            END IF;

        WHEN 'yearly' THEN
            v_next := p_current_date + (p_interval || ' years')::INTERVAL;
            IF p_day_of_month IS NOT NULL THEN
                v_next := DATE_TRUNC('month', v_next) + ((p_day_of_month - 1) || ' days')::INTERVAL;
            END IF;

        ELSE
            RAISE EXCEPTION 'Invalid frequency: %', p_frequency;
    END CASE;

    RETURN v_next;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION calculate_next_invoice_date IS 'Calculates next invoice date based on frequency and interval';

-- Get recurring invoices due for generation
CREATE OR REPLACE FUNCTION get_due_recurring_invoices(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
) RETURNS TABLE(
    id UUID,
    template_name VARCHAR,
    customer_id UUID,
    customer_name VARCHAR,
    next_invoice_date DATE,
    total_amount BIGINT,
    invoices_generated INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ri.id,
        ri.template_name,
        ri.customer_id,
        ri.customer_name,
        ri.next_invoice_date,
        ri.total_amount,
        ri.invoices_generated
    FROM recurring_invoices ri
    WHERE ri.tenant_id = p_tenant_id
    AND ri.status = 'active'
    AND ri.next_invoice_date <= p_as_of_date
    AND (ri.end_date IS NULL OR ri.end_date >= p_as_of_date)
    ORDER BY ri.next_invoice_date ASC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_due_recurring_invoices IS 'Returns recurring invoices due for generation';

-- ============================================================================
-- 7. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_recurring_invoices_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_recurring_invoices_updated_at ON recurring_invoices;
CREATE TRIGGER trg_recurring_invoices_updated_at
    BEFORE UPDATE ON recurring_invoices
    FOR EACH ROW EXECUTE FUNCTION update_recurring_invoices_updated_at();

-- Auto-calculate recurring invoice totals
CREATE OR REPLACE FUNCTION update_recurring_invoice_totals()
RETURNS TRIGGER AS $$
DECLARE
    v_ri_id UUID;
    v_subtotal BIGINT;
    v_tax BIGINT;
    v_discount BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_ri_id := OLD.recurring_invoice_id;
    ELSE
        v_ri_id := NEW.recurring_invoice_id;
    END IF;

    SELECT
        COALESCE(SUM(subtotal), 0),
        COALESCE(SUM(tax_amount), 0),
        COALESCE(SUM(discount_amount), 0)
    INTO v_subtotal, v_tax, v_discount
    FROM recurring_invoice_items
    WHERE recurring_invoice_id = v_ri_id;

    UPDATE recurring_invoices
    SET subtotal = v_subtotal,
        tax_amount = v_tax,
        total_amount = v_subtotal - discount_amount + v_tax,
        updated_at = NOW()
    WHERE id = v_ri_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_ri_totals ON recurring_invoice_items;
CREATE TRIGGER trg_update_ri_totals
    AFTER INSERT OR UPDATE OR DELETE ON recurring_invoice_items
    FOR EACH ROW EXECUTE FUNCTION update_recurring_invoice_totals();

-- Check if recurring invoice should be completed (end_date reached)
CREATE OR REPLACE FUNCTION check_recurring_invoice_completion()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.end_date IS NOT NULL AND NEW.next_invoice_date > NEW.end_date THEN
        NEW.status := 'completed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_ri_completion ON recurring_invoices;
CREATE TRIGGER trg_check_ri_completion
    BEFORE UPDATE ON recurring_invoices
    FOR EACH ROW EXECUTE FUNCTION check_recurring_invoice_completion();

-- ============================================================================
-- 8. INVOICE GENERATION NOTES (Implementation in router)
-- ============================================================================

/*
Generate Invoice Process:

1. Get recurring invoice template
2. Create sales_invoice from template:
   - Copy customer_id, warehouse_id
   - Set invoice_date = next_invoice_date
   - Set due_date = invoice_date + due_days
   - Copy line items
   - Set recurring_invoice_id, is_recurring = true
3. If auto_post = true:
   - Post invoice to accounting
4. If auto_send = true:
   - Queue email to customer
5. Update recurring_invoice:
   - next_invoice_date = calculate_next_invoice_date()
   - last_invoice_date = CURRENT_DATE
   - invoices_generated += 1
   - total_invoiced += invoice.total_amount
6. Check if end_date reached:
   - If next_invoice_date > end_date, status = 'completed'
*/

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V046: Recurring Invoices created successfully';
    RAISE NOTICE 'Tables: recurring_invoices, recurring_invoice_items';
    RAISE NOTICE 'Extended: sales_invoices with recurring_invoice_id';
    RAISE NOTICE 'Frequencies: daily, weekly, monthly, quarterly, yearly';
    RAISE NOTICE 'Functions: calculate_next_invoice_date, get_due_recurring_invoices';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
