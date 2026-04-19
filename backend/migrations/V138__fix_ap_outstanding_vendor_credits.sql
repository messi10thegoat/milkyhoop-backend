DROP FUNCTION IF EXISTS get_ap_aging_summary(text,date);
-- V138: Fix compute_ap_outstanding() to include VENDOR_CREDIT journals on PAYABLE
-- Problem: VENDOR_CREDIT debits (reduce AP) and their void reversals (credit, increase AP)
-- were not captured, causing AP drift of 115K in grapgrap tenant.
-- Also updates get_ap_aging_summary() return types from BIGINT to NUMERIC(18,2).

CREATE OR REPLACE FUNCTION compute_ap_outstanding(p_tenant_id TEXT)
RETURNS TABLE(
    vendor_id   UUID,
    vendor_name TEXT,
    bill_id     UUID,
    bill_number TEXT,
    bill_date   DATE,
    due_date    DATE,
    bill_status TEXT,
    bill_total  NUMERIC,
    paid_amount NUMERIC,
    outstanding NUMERIC
) LANGUAGE sql STABLE AS $$
    WITH
    -- 1. Active bills (non-draft, non-void)
    active_bills AS (
        SELECT b.id, b.invoice_number, b.issue_date, b.due_date,
               b.vendor_id, b.vendor_name, b.status_v2, b.amount
        FROM bills b
        WHERE b.tenant_id = p_tenant_id
          AND b.status_v2 NOT IN ('draft', 'void')
    ),
    -- 2. Bill obligation: credits on PAYABLE from BILL journals
    bill_credits AS (
        SELECT je.source_id::uuid AS bill_id,
               COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE'
          AND jl.credit > 0
          AND je.source_type = 'BILL'
        GROUP BY je.source_id::uuid
    ),
    -- 3. Bill payments: debits on PAYABLE from bill_payment_allocations
    payment_debits AS (
        SELECT bpa.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM bill_payment_allocations bpa
        JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
        JOIN journal_entries je ON je.id = bpv2.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE bpv2.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY bpa.bill_id
    ),
    -- 4. Vendor credit applications to specific bills (debits on PAYABLE)
    vc_applied_debits AS (
        SELECT vca.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM vendor_credit_applications vca
        JOIN journal_entries je ON je.id = vca.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE vca.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY vca.bill_id
    ),
    -- 5. Unapplied vendor credits: VENDOR_CREDIT journals on PAYABLE not tied to bills
    --    These are net debit/credit on PAYABLE per vendor.
    --    Debits = VC creation (reduce AP), Credits = VC void reversals (increase AP).
    vc_unapplied AS (
        SELECT vc.vendor_id,
               vc.vendor_name,
               COALESCE(SUM(jl.credit), 0) AS total_credit,
               COALESCE(SUM(jl.debit), 0)  AS total_debit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        JOIN vendor_credits vc ON vc.id = je.source_id::uuid
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND je.source_type = 'VENDOR_CREDIT'
          AND coa.account_type = 'PAYABLE'
          -- Exclude applied VCs (they're handled in vc_applied_debits)
          AND NOT EXISTS (
              SELECT 1 FROM vendor_credit_applications vca
              WHERE vca.vendor_credit_id = vc.id
                AND vca.journal_id = je.id
          )
        GROUP BY vc.vendor_id, vc.vendor_name
    ),
    -- Combine bill rows
    bill_rows AS (
        SELECT ab.vendor_id, ab.vendor_name::TEXT, ab.id AS bill_id,
               ab.invoice_number::TEXT AS bill_number, ab.issue_date AS bill_date,
               ab.due_date, ab.status_v2::TEXT AS bill_status,
               COALESCE(bc.total_credit, 0)::NUMERIC(18,2) AS bill_total,
               (COALESCE(pd.total_debit, 0) + COALESCE(vcd.total_debit, 0))::NUMERIC(18,2) AS paid_amount,
               (COALESCE(bc.total_credit, 0) - COALESCE(pd.total_debit, 0) - COALESCE(vcd.total_debit, 0))::NUMERIC(18,2) AS outstanding
        FROM active_bills ab
        LEFT JOIN bill_credits bc ON bc.bill_id = ab.id
        LEFT JOIN payment_debits pd ON pd.bill_id = ab.id
        LEFT JOIN vc_applied_debits vcd ON vcd.bill_id = ab.id
    ),
    -- Unapplied VC rows (synthetic — no bill, vendor-level)
    vc_rows AS (
        SELECT vcu.vendor_id,
               vcu.vendor_name::TEXT,
               NULL::UUID AS bill_id,
               'VENDOR-CREDIT'::TEXT AS bill_number,
               CURRENT_DATE AS bill_date,
               CURRENT_DATE AS due_date,
               'vendor_credit'::TEXT AS bill_status,
               vcu.total_credit::NUMERIC(18,2) AS bill_total,
               vcu.total_debit::NUMERIC(18,2) AS paid_amount,
               (vcu.total_credit - vcu.total_debit)::NUMERIC(18,2) AS outstanding
        FROM vc_unapplied vcu
    )
    SELECT * FROM bill_rows WHERE outstanding != 0
    UNION ALL
    SELECT * FROM vc_rows WHERE outstanding != 0
    ORDER BY vendor_name, due_date;
$$;

-- get_ap_aging_summary already delegates to compute_ap_outstanding(), so it
-- automatically picks up the fix.  But fix the return types from BIGINT to
-- NUMERIC(18,2) for consistency with the underlying data.
CREATE OR REPLACE FUNCTION get_ap_aging_summary(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE(
    total_current  NUMERIC(18,2),
    total_1_30     NUMERIC(18,2),
    total_31_60    NUMERIC(18,2),
    total_61_90    NUMERIC(18,2),
    total_91_120   NUMERIC(18,2),
    total_over_120 NUMERIC(18,2),
    grand_total    NUMERIC(18,2),
    overdue_count  BIGINT
) LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
    WITH ap AS (
        SELECT a.outstanding, a.due_date, a.bill_date
        FROM compute_ap_outstanding(p_tenant_id) a
        WHERE a.bill_date <= p_as_of_date
    ),
    bucketed AS (
        SELECT
            ap.outstanding,
            CASE
                WHEN p_as_of_date <= ap.due_date THEN 'current'
                WHEN (p_as_of_date - ap.due_date) BETWEEN 1 AND 30 THEN 'bracket_1'
                WHEN (p_as_of_date - ap.due_date) BETWEEN 31 AND 60 THEN 'bracket_2'
                WHEN (p_as_of_date - ap.due_date) BETWEEN 61 AND 90 THEN 'bracket_3'
                WHEN (p_as_of_date - ap.due_date) BETWEEN 91 AND 120 THEN 'bracket_4'
                ELSE 'bracket_5'
            END AS aging_bucket
        FROM ap
    )
    SELECT
        COALESCE(SUM(CASE WHEN aging_bucket = 'current' THEN bucketed.outstanding ELSE 0 END), 0)::NUMERIC(18,2),
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_1' THEN bucketed.outstanding ELSE 0 END), 0)::NUMERIC(18,2),
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_2' THEN bucketed.outstanding ELSE 0 END), 0)::NUMERIC(18,2),
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_3' THEN bucketed.outstanding ELSE 0 END), 0)::NUMERIC(18,2),
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_4' THEN bucketed.outstanding ELSE 0 END), 0)::NUMERIC(18,2),
        COALESCE(SUM(CASE WHEN aging_bucket = 'bracket_5' THEN bucketed.outstanding ELSE 0 END), 0)::NUMERIC(18,2),
        COALESCE(SUM(bucketed.outstanding), 0)::NUMERIC(18,2),
        COUNT(CASE WHEN aging_bucket != 'current' THEN 1 END)::BIGINT
    FROM bucketed;
END;
$$;
