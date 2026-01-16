-- =============================================
-- V055: AR/AP Aging Reports (Umur Piutang & Hutang)
-- Purpose: Aging analysis for receivables and payables
-- =============================================

-- Aging brackets configuration
CREATE TABLE aging_brackets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    bracket_type VARCHAR(10) NOT NULL, -- ar, ap

    -- Brackets (in days)
    bracket_1_days INTEGER DEFAULT 30,  -- 0-30
    bracket_2_days INTEGER DEFAULT 60,  -- 31-60
    bracket_3_days INTEGER DEFAULT 90,  -- 61-90
    bracket_4_days INTEGER DEFAULT 120, -- 91-120
    -- > bracket_4_days = 120+

    -- Labels
    bracket_1_label VARCHAR(50) DEFAULT 'Current',
    bracket_2_label VARCHAR(50) DEFAULT '1-30 Days',
    bracket_3_label VARCHAR(50) DEFAULT '31-60 Days',
    bracket_4_label VARCHAR(50) DEFAULT '61-90 Days',
    bracket_5_label VARCHAR(50) DEFAULT '90+ Days',

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_aging_brackets UNIQUE(tenant_id, bracket_type)
);

-- Materialized aging snapshot (for performance, optional - run daily/weekly)
CREATE TABLE aging_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    snapshot_date DATE NOT NULL,
    snapshot_type VARCHAR(10) NOT NULL, -- ar, ap

    -- Summary
    total_current BIGINT DEFAULT 0,
    total_bracket_1 BIGINT DEFAULT 0,
    total_bracket_2 BIGINT DEFAULT 0,
    total_bracket_3 BIGINT DEFAULT 0,
    total_bracket_4 BIGINT DEFAULT 0,
    total_overdue BIGINT DEFAULT 0,
    grand_total BIGINT DEFAULT 0,

    -- Detail data
    detail_data JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_aging_snapshots UNIQUE(tenant_id, snapshot_date, snapshot_type)
);

-- RLS
ALTER TABLE aging_brackets ENABLE ROW LEVEL SECURITY;
ALTER TABLE aging_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_aging_brackets ON aging_brackets
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_aging_snapshots ON aging_snapshots
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Indexes
CREATE INDEX idx_aging_brackets_tenant ON aging_brackets(tenant_id);
CREATE INDEX idx_aging_snapshots_tenant ON aging_snapshots(tenant_id, snapshot_date);

-- =============================================
-- AR Aging Functions
-- =============================================

-- AR Aging Summary
CREATE OR REPLACE FUNCTION get_ar_aging_summary(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    total_current BIGINT,
    total_1_30 BIGINT,
    total_31_60 BIGINT,
    total_61_90 BIGINT,
    total_91_120 BIGINT,
    total_over_120 BIGINT,
    grand_total BIGINT,
    overdue_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH invoice_aging AS (
        SELECT
            si.id,
            si.total_amount - COALESCE(si.paid_amount, 0) as balance,
            CASE
                WHEN p_as_of_date <= si.due_date THEN 'current'
                WHEN (p_as_of_date - si.due_date) BETWEEN 1 AND 30 THEN 'bracket_1'
                WHEN (p_as_of_date - si.due_date) BETWEEN 31 AND 60 THEN 'bracket_2'
                WHEN (p_as_of_date - si.due_date) BETWEEN 61 AND 90 THEN 'bracket_3'
                WHEN (p_as_of_date - si.due_date) BETWEEN 91 AND 120 THEN 'bracket_4'
                ELSE 'bracket_5'
            END as aging_bucket
        FROM sales_invoices si
        WHERE si.tenant_id = p_tenant_id
        AND si.status IN ('posted', 'partial')
        AND si.invoice_date <= p_as_of_date
        AND (si.total_amount - COALESCE(si.paid_amount, 0)) > 0
    )
    SELECT
        COALESCE(SUM(CASE WHEN aging_bucket = 'current' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_1' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_2' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_3' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_4' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_5' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(balance), 0)::BIGINT,
        COUNT(CASE WHEN aging_bucket != 'current' THEN 1 END)::BIGINT
    FROM invoice_aging;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- AR Aging Detail by Customer
CREATE OR REPLACE FUNCTION get_ar_aging_detail(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    customer_id UUID,
    customer_name VARCHAR(255),
    customer_code VARCHAR(50),
    current_amount BIGINT,
    days_1_30 BIGINT,
    days_31_60 BIGINT,
    days_61_90 BIGINT,
    days_91_120 BIGINT,
    days_over_120 BIGINT,
    total_balance BIGINT,
    oldest_invoice_date DATE,
    invoice_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH invoice_aging AS (
        SELECT
            si.customer_id,
            c.name as customer_name,
            c.code as customer_code,
            si.invoice_date,
            si.total_amount - COALESCE(si.paid_amount, 0) as balance,
            CASE
                WHEN p_as_of_date <= si.due_date THEN 'current'
                WHEN (p_as_of_date - si.due_date) BETWEEN 1 AND 30 THEN 'bracket_1'
                WHEN (p_as_of_date - si.due_date) BETWEEN 31 AND 60 THEN 'bracket_2'
                WHEN (p_as_of_date - si.due_date) BETWEEN 61 AND 90 THEN 'bracket_3'
                WHEN (p_as_of_date - si.due_date) BETWEEN 91 AND 120 THEN 'bracket_4'
                ELSE 'bracket_5'
            END as aging_bucket
        FROM sales_invoices si
        JOIN customers c ON si.customer_id = c.id
        WHERE si.tenant_id = p_tenant_id
        AND si.status IN ('posted', 'partial')
        AND si.invoice_date <= p_as_of_date
        AND (si.total_amount - COALESCE(si.paid_amount, 0)) > 0
    )
    SELECT
        ia.customer_id,
        ia.customer_name,
        ia.customer_code,
        COALESCE(SUM(CASE WHEN aging_bucket = 'current' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_1' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_2' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_3' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_4' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_5' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(balance), 0)::BIGINT,
        MIN(ia.invoice_date),
        COUNT(*)::BIGINT
    FROM invoice_aging ia
    GROUP BY ia.customer_id, ia.customer_name, ia.customer_code
    ORDER BY SUM(balance) DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Single Customer AR Aging
CREATE OR REPLACE FUNCTION get_ar_aging_customer(
    p_customer_id UUID,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    invoice_id UUID,
    invoice_number VARCHAR(50),
    invoice_date DATE,
    due_date DATE,
    total_amount BIGINT,
    paid_amount BIGINT,
    balance BIGINT,
    days_overdue INTEGER,
    aging_bucket VARCHAR(20)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        si.id as invoice_id,
        si.invoice_number,
        si.invoice_date,
        si.due_date,
        si.total_amount,
        COALESCE(si.paid_amount, 0)::BIGINT as paid_amount,
        (si.total_amount - COALESCE(si.paid_amount, 0))::BIGINT as balance,
        GREATEST(0, p_as_of_date - si.due_date)::INTEGER as days_overdue,
        CASE
            WHEN p_as_of_date <= si.due_date THEN 'current'
            WHEN (p_as_of_date - si.due_date) BETWEEN 1 AND 30 THEN '1-30 days'
            WHEN (p_as_of_date - si.due_date) BETWEEN 31 AND 60 THEN '31-60 days'
            WHEN (p_as_of_date - si.due_date) BETWEEN 61 AND 90 THEN '61-90 days'
            WHEN (p_as_of_date - si.due_date) BETWEEN 91 AND 120 THEN '91-120 days'
            ELSE '120+ days'
        END as aging_bucket
    FROM sales_invoices si
    WHERE si.customer_id = p_customer_id
    AND si.status IN ('posted', 'partial')
    AND si.invoice_date <= p_as_of_date
    AND (si.total_amount - COALESCE(si.paid_amount, 0)) > 0
    ORDER BY si.due_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- AP Aging Functions
-- =============================================

-- AP Aging Summary
CREATE OR REPLACE FUNCTION get_ap_aging_summary(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    total_current BIGINT,
    total_1_30 BIGINT,
    total_31_60 BIGINT,
    total_61_90 BIGINT,
    total_91_120 BIGINT,
    total_over_120 BIGINT,
    grand_total BIGINT,
    overdue_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH bill_aging AS (
        SELECT
            b.id,
            b.total_amount - COALESCE(b.paid_amount, 0) as balance,
            CASE
                WHEN p_as_of_date <= b.due_date THEN 'current'
                WHEN (p_as_of_date - b.due_date) BETWEEN 1 AND 30 THEN 'bracket_1'
                WHEN (p_as_of_date - b.due_date) BETWEEN 31 AND 60 THEN 'bracket_2'
                WHEN (p_as_of_date - b.due_date) BETWEEN 61 AND 90 THEN 'bracket_3'
                WHEN (p_as_of_date - b.due_date) BETWEEN 91 AND 120 THEN 'bracket_4'
                ELSE 'bracket_5'
            END as aging_bucket
        FROM bills b
        WHERE b.tenant_id = p_tenant_id
        AND b.status IN ('posted', 'partial')
        AND b.bill_date <= p_as_of_date
        AND (b.total_amount - COALESCE(b.paid_amount, 0)) > 0
    )
    SELECT
        COALESCE(SUM(CASE WHEN aging_bucket = 'current' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_1' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_2' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_3' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_4' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_5' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(balance), 0)::BIGINT,
        COUNT(CASE WHEN aging_bucket != 'current' THEN 1 END)::BIGINT
    FROM bill_aging;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- AP Aging Detail by Vendor
CREATE OR REPLACE FUNCTION get_ap_aging_detail(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    vendor_id UUID,
    vendor_name VARCHAR(255),
    vendor_code VARCHAR(50),
    current_amount BIGINT,
    days_1_30 BIGINT,
    days_31_60 BIGINT,
    days_61_90 BIGINT,
    days_91_120 BIGINT,
    days_over_120 BIGINT,
    total_balance BIGINT,
    oldest_bill_date DATE,
    bill_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH bill_aging AS (
        SELECT
            b.vendor_id,
            v.name as vendor_name,
            v.code as vendor_code,
            b.bill_date,
            b.total_amount - COALESCE(b.paid_amount, 0) as balance,
            CASE
                WHEN p_as_of_date <= b.due_date THEN 'current'
                WHEN (p_as_of_date - b.due_date) BETWEEN 1 AND 30 THEN 'bracket_1'
                WHEN (p_as_of_date - b.due_date) BETWEEN 31 AND 60 THEN 'bracket_2'
                WHEN (p_as_of_date - b.due_date) BETWEEN 61 AND 90 THEN 'bracket_3'
                WHEN (p_as_of_date - b.due_date) BETWEEN 91 AND 120 THEN 'bracket_4'
                ELSE 'bracket_5'
            END as aging_bucket
        FROM bills b
        JOIN vendors v ON b.vendor_id = v.id
        WHERE b.tenant_id = p_tenant_id
        AND b.status IN ('posted', 'partial')
        AND b.bill_date <= p_as_of_date
        AND (b.total_amount - COALESCE(b.paid_amount, 0)) > 0
    )
    SELECT
        ba.vendor_id,
        ba.vendor_name,
        ba.vendor_code,
        COALESCE(SUM(CASE WHEN aging_bucket = 'current' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_1' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_2' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_3' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_4' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_5' THEN balance ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(balance), 0)::BIGINT,
        MIN(ba.bill_date),
        COUNT(*)::BIGINT
    FROM bill_aging ba
    GROUP BY ba.vendor_id, ba.vendor_name, ba.vendor_code
    ORDER BY SUM(balance) DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Single Vendor AP Aging
CREATE OR REPLACE FUNCTION get_ap_aging_vendor(
    p_vendor_id UUID,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    bill_id UUID,
    bill_number VARCHAR(50),
    bill_date DATE,
    due_date DATE,
    total_amount BIGINT,
    paid_amount BIGINT,
    balance BIGINT,
    days_overdue INTEGER,
    aging_bucket VARCHAR(20)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        b.id as bill_id,
        b.bill_number,
        b.bill_date,
        b.due_date,
        b.total_amount,
        COALESCE(b.paid_amount, 0)::BIGINT as paid_amount,
        (b.total_amount - COALESCE(b.paid_amount, 0))::BIGINT as balance,
        GREATEST(0, p_as_of_date - b.due_date)::INTEGER as days_overdue,
        CASE
            WHEN p_as_of_date <= b.due_date THEN 'current'
            WHEN (p_as_of_date - b.due_date) BETWEEN 1 AND 30 THEN '1-30 days'
            WHEN (p_as_of_date - b.due_date) BETWEEN 31 AND 60 THEN '31-60 days'
            WHEN (p_as_of_date - b.due_date) BETWEEN 61 AND 90 THEN '61-90 days'
            WHEN (p_as_of_date - b.due_date) BETWEEN 91 AND 120 THEN '91-120 days'
            ELSE '120+ days'
        END as aging_bucket
    FROM bills b
    WHERE b.vendor_id = p_vendor_id
    AND b.status IN ('posted', 'partial')
    AND b.bill_date <= p_as_of_date
    AND (b.total_amount - COALESCE(b.paid_amount, 0)) > 0
    ORDER BY b.due_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- Snapshot Functions
-- =============================================

-- Create AR aging snapshot
CREATE OR REPLACE FUNCTION create_ar_aging_snapshot(p_tenant_id TEXT, p_as_of_date DATE DEFAULT CURRENT_DATE)
RETURNS UUID AS $$
DECLARE
    v_snapshot_id UUID;
    v_summary RECORD;
    v_detail JSONB;
BEGIN
    -- Get summary
    SELECT * INTO v_summary FROM get_ar_aging_summary(p_tenant_id, p_as_of_date);

    -- Get detail as JSON
    SELECT jsonb_agg(row_to_json(d))
    INTO v_detail
    FROM get_ar_aging_detail(p_tenant_id, p_as_of_date) d;

    -- Insert snapshot
    INSERT INTO aging_snapshots (
        tenant_id, snapshot_date, snapshot_type,
        total_current, total_bracket_1, total_bracket_2,
        total_bracket_3, total_bracket_4, total_overdue,
        grand_total, detail_data
    ) VALUES (
        p_tenant_id, p_as_of_date, 'ar',
        v_summary.total_current, v_summary.total_1_30, v_summary.total_31_60,
        v_summary.total_61_90, v_summary.total_91_120,
        v_summary.total_1_30 + v_summary.total_31_60 + v_summary.total_61_90 +
            v_summary.total_91_120 + v_summary.total_over_120,
        v_summary.grand_total, v_detail
    )
    ON CONFLICT (tenant_id, snapshot_date, snapshot_type)
    DO UPDATE SET
        total_current = EXCLUDED.total_current,
        total_bracket_1 = EXCLUDED.total_bracket_1,
        total_bracket_2 = EXCLUDED.total_bracket_2,
        total_bracket_3 = EXCLUDED.total_bracket_3,
        total_bracket_4 = EXCLUDED.total_bracket_4,
        total_overdue = EXCLUDED.total_overdue,
        grand_total = EXCLUDED.grand_total,
        detail_data = EXCLUDED.detail_data,
        created_at = NOW()
    RETURNING id INTO v_snapshot_id;

    RETURN v_snapshot_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Create AP aging snapshot
CREATE OR REPLACE FUNCTION create_ap_aging_snapshot(p_tenant_id TEXT, p_as_of_date DATE DEFAULT CURRENT_DATE)
RETURNS UUID AS $$
DECLARE
    v_snapshot_id UUID;
    v_summary RECORD;
    v_detail JSONB;
BEGIN
    -- Get summary
    SELECT * INTO v_summary FROM get_ap_aging_summary(p_tenant_id, p_as_of_date);

    -- Get detail as JSON
    SELECT jsonb_agg(row_to_json(d))
    INTO v_detail
    FROM get_ap_aging_detail(p_tenant_id, p_as_of_date) d;

    -- Insert snapshot
    INSERT INTO aging_snapshots (
        tenant_id, snapshot_date, snapshot_type,
        total_current, total_bracket_1, total_bracket_2,
        total_bracket_3, total_bracket_4, total_overdue,
        grand_total, detail_data
    ) VALUES (
        p_tenant_id, p_as_of_date, 'ap',
        v_summary.total_current, v_summary.total_1_30, v_summary.total_31_60,
        v_summary.total_61_90, v_summary.total_91_120,
        v_summary.total_1_30 + v_summary.total_31_60 + v_summary.total_61_90 +
            v_summary.total_91_120 + v_summary.total_over_120,
        v_summary.grand_total, v_detail
    )
    ON CONFLICT (tenant_id, snapshot_date, snapshot_type)
    DO UPDATE SET
        total_current = EXCLUDED.total_current,
        total_bracket_1 = EXCLUDED.total_bracket_1,
        total_bracket_2 = EXCLUDED.total_bracket_2,
        total_bracket_3 = EXCLUDED.total_bracket_3,
        total_bracket_4 = EXCLUDED.total_bracket_4,
        total_overdue = EXCLUDED.total_overdue,
        grand_total = EXCLUDED.grand_total,
        detail_data = EXCLUDED.detail_data,
        created_at = NOW()
    RETURNING id INTO v_snapshot_id;

    RETURN v_snapshot_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get aging trend (from snapshots)
CREATE OR REPLACE FUNCTION get_aging_trend(
    p_tenant_id TEXT,
    p_type VARCHAR(10), -- 'ar' or 'ap'
    p_start_date DATE,
    p_end_date DATE
)
RETURNS TABLE (
    snapshot_date DATE,
    total_current BIGINT,
    total_overdue BIGINT,
    grand_total BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.snapshot_date,
        s.total_current,
        s.total_overdue,
        s.grand_total
    FROM aging_snapshots s
    WHERE s.tenant_id = p_tenant_id
    AND s.snapshot_type = p_type
    AND s.snapshot_date BETWEEN p_start_date AND p_end_date
    ORDER BY s.snapshot_date;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================
-- Comments
-- =============================================
COMMENT ON TABLE aging_brackets IS 'Configurable aging brackets for AR/AP reports';
COMMENT ON TABLE aging_snapshots IS 'Point-in-time snapshots for trend analysis';
COMMENT ON FUNCTION get_ar_aging_summary IS 'Get AR aging summary by bracket';
COMMENT ON FUNCTION get_ap_aging_summary IS 'Get AP aging summary by bracket';
