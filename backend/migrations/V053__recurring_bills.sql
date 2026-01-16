-- =============================================
-- V053: Recurring Bills (Tagihan Berulang)
-- Purpose: Auto-generate bills on schedule (mirror of recurring invoices)
-- =============================================

-- Recurring bill templates
CREATE TABLE recurring_bills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Template info
    template_name VARCHAR(100) NOT NULL,

    -- Vendor
    vendor_id UUID NOT NULL REFERENCES vendors(id),

    -- Schedule
    frequency VARCHAR(20) NOT NULL, -- daily, weekly, monthly, quarterly, yearly
    interval_count INTEGER DEFAULT 1,

    -- Dates
    start_date DATE NOT NULL,
    end_date DATE,
    next_bill_date DATE NOT NULL,
    last_bill_date DATE,

    -- Bill defaults
    due_days INTEGER DEFAULT 30,

    -- Amounts (template)
    subtotal BIGINT NOT NULL,
    discount_amount BIGINT DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL,

    -- Settings
    auto_post BOOLEAN DEFAULT false,

    -- Status
    status VARCHAR(20) DEFAULT 'active', -- active, paused, completed, cancelled

    -- Stats
    bills_generated INTEGER DEFAULT 0,

    -- Notes
    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID
);

-- Recurring bill items
CREATE TABLE recurring_bill_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recurring_bill_id UUID NOT NULL REFERENCES recurring_bills(id) ON DELETE CASCADE,

    item_id UUID REFERENCES products(id),
    description TEXT NOT NULL,

    quantity DECIMAL(15,4) NOT NULL,
    unit_price BIGINT NOT NULL,

    -- Account for expense
    account_id UUID REFERENCES chart_of_accounts(id),
    cost_center_id UUID REFERENCES cost_centers(id),

    tax_id UUID,  -- No FK - tax config may be stored differently
    tax_amount BIGINT DEFAULT 0,
    line_total BIGINT NOT NULL,

    sort_order INTEGER DEFAULT 0
);

-- Link generated bills back to template
ALTER TABLE bills ADD COLUMN IF NOT EXISTS recurring_bill_id UUID REFERENCES recurring_bills(id);
ALTER TABLE bills ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT false;

-- RLS
ALTER TABLE recurring_bills ENABLE ROW LEVEL SECURITY;
ALTER TABLE recurring_bill_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_recurring_bills ON recurring_bills
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_recurring_bill_items ON recurring_bill_items
    USING (recurring_bill_id IN (SELECT id FROM recurring_bills WHERE tenant_id = current_setting('app.tenant_id', true)));

-- Indexes
CREATE INDEX idx_recurring_bills_tenant ON recurring_bills(tenant_id);
CREATE INDEX idx_recurring_bills_vendor ON recurring_bills(vendor_id);
CREATE INDEX idx_recurring_bills_next ON recurring_bills(tenant_id, next_bill_date) WHERE status = 'active';
CREATE INDEX idx_recurring_bills_status ON recurring_bills(tenant_id, status);
CREATE INDEX idx_bills_recurring ON bills(recurring_bill_id) WHERE recurring_bill_id IS NOT NULL;

-- =============================================
-- Helper Functions
-- =============================================

-- Calculate next bill date based on frequency
CREATE OR REPLACE FUNCTION calculate_next_bill_date(
    p_current_date DATE,
    p_frequency VARCHAR(20),
    p_interval_count INTEGER DEFAULT 1
)
RETURNS DATE AS $$
BEGIN
    RETURN CASE p_frequency
        WHEN 'daily' THEN p_current_date + (p_interval_count || ' days')::INTERVAL
        WHEN 'weekly' THEN p_current_date + (p_interval_count || ' weeks')::INTERVAL
        WHEN 'monthly' THEN p_current_date + (p_interval_count || ' months')::INTERVAL
        WHEN 'quarterly' THEN p_current_date + (p_interval_count * 3 || ' months')::INTERVAL
        WHEN 'yearly' THEN p_current_date + (p_interval_count || ' years')::INTERVAL
        ELSE p_current_date + (p_interval_count || ' months')::INTERVAL
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Get recurring bills due for generation
CREATE OR REPLACE FUNCTION get_due_recurring_bills(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    id UUID,
    template_name VARCHAR(100),
    vendor_id UUID,
    vendor_name VARCHAR(255),
    next_bill_date DATE,
    frequency VARCHAR(20),
    total_amount BIGINT,
    auto_post BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        rb.id,
        rb.template_name,
        rb.vendor_id,
        v.name as vendor_name,
        rb.next_bill_date,
        rb.frequency,
        rb.total_amount,
        rb.auto_post
    FROM recurring_bills rb
    JOIN vendors v ON rb.vendor_id = v.id
    WHERE rb.tenant_id = p_tenant_id
    AND rb.status = 'active'
    AND rb.next_bill_date <= p_as_of_date
    AND (rb.end_date IS NULL OR rb.next_bill_date <= rb.end_date)
    ORDER BY rb.next_bill_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get generated bills for a recurring template
CREATE OR REPLACE FUNCTION get_recurring_bill_history(p_recurring_bill_id UUID)
RETURNS TABLE (
    bill_id UUID,
    bill_number VARCHAR(50),
    bill_date DATE,
    due_date DATE,
    total_amount BIGINT,
    status VARCHAR(20),
    paid_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        b.id as bill_id,
        b.bill_number,
        b.bill_date,
        b.due_date,
        b.total_amount,
        b.status,
        COALESCE(b.paid_amount, 0)::BIGINT as paid_amount
    FROM bills b
    WHERE b.recurring_bill_id = p_recurring_bill_id
    ORDER BY b.bill_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get recurring bill statistics
CREATE OR REPLACE FUNCTION get_recurring_bill_stats(p_tenant_id TEXT)
RETURNS TABLE (
    total_active INTEGER,
    total_paused INTEGER,
    total_completed INTEGER,
    bills_generated_this_month INTEGER,
    total_amount_this_month BIGINT,
    due_today INTEGER,
    due_this_week INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id AND status = 'active'),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id AND status = 'paused'),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id AND status = 'completed'),
        (SELECT COUNT(*)::INTEGER FROM bills WHERE tenant_id = p_tenant_id
            AND is_recurring = true AND EXTRACT(MONTH FROM bill_date) = EXTRACT(MONTH FROM CURRENT_DATE)
            AND EXTRACT(YEAR FROM bill_date) = EXTRACT(YEAR FROM CURRENT_DATE)),
        (SELECT COALESCE(SUM(total_amount), 0)::BIGINT FROM bills WHERE tenant_id = p_tenant_id
            AND is_recurring = true AND EXTRACT(MONTH FROM bill_date) = EXTRACT(MONTH FROM CURRENT_DATE)
            AND EXTRACT(YEAR FROM bill_date) = EXTRACT(YEAR FROM CURRENT_DATE)),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id
            AND status = 'active' AND next_bill_date = CURRENT_DATE),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id
            AND status = 'active' AND next_bill_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days');
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- Comments
-- =============================================
COMMENT ON TABLE recurring_bills IS 'Templates for auto-generating bills on schedule';
COMMENT ON COLUMN recurring_bills.frequency IS 'daily, weekly, monthly, quarterly, yearly';
COMMENT ON COLUMN recurring_bills.interval_count IS 'Number of frequency units between bills (e.g., 2 = every 2 months)';
COMMENT ON COLUMN recurring_bills.auto_post IS 'If true, generated bills are automatically posted';
