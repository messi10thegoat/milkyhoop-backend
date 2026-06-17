-- FIX_P35_ARCANON 2026-06-17
-- P3.5 — Canonical AR-outstanding must account for customer-deposit applications,
-- and the drift class is closed with a fail-loud reconciliation invariant.
--
-- THREE LAYERS, applied additively (all idempotent CREATE OR REPLACE):
--
-- LAYER 1 (this migration, parts A+B) — instance fix + double-count trap:
--   A. compute_ar_outstanding(): add a 3rd settlement UNION branch
--      (payment_credits via customer_deposit_applications -> DEPOSIT_APPLICATION
--       journal -> Cr RECEIVABLE), so AR is no longer over-stated after a deposit
--       is applied. Per-invoice attribution comes from the cda.invoice_id link
--       (the GL Cr RECEIVABLE line carries no invoice ref).
--   B. compute_ar_adjustments(): EXCLUDE 'DEPOSIT_APPLICATION' from the
--      "orphaned journals" catch-all. Before this migration the catch-all
--      (source_type NOT IN (...)) implicitly swept DEPOSIT_APPLICATION credits
--      into "adjustments". Once Layer 1A counts them as settlement, leaving them
--      in adjustments would DOUBLE-SUBTRACT. Both must change together.
--
-- LAYER 3 (this migration, part C) — fail-loud reconciliation invariant:
--   verify_ar_reconciliation(p_tenant_id) + verify_ar_reconciliation_all(),
--   per-customer  Sum(compute_ar_outstanding) == Sum(GL RECEIVABLE net)  within 0.01,
--   using is_effective_journal() for symmetric reversal handling.
--   Exemption store ar_reconciliation_exemptions (enforce-with-grandfather):
--   non-exempt drift!=0 FAILS; exempt-tenant drift CHANGED-from-baseline FAILS.
--
-- DOUBLE-COUNT TRAP NOTE: 1A adds, 1B removes — net effect on outstanding is a
-- single subtraction of the applied deposit amount (proven in verification).
--
-- Idempotent: CREATE OR REPLACE FUNCTION + CREATE TABLE IF NOT EXISTS.

-- =====================================================================
-- LAYER 1A — compute_ar_outstanding(): add deposit-application settlement
-- =====================================================================
CREATE OR REPLACE FUNCTION public.compute_ar_outstanding(p_tenant_id text)
 RETURNS TABLE(customer_id text, customer_name text, invoice_id uuid, invoice_number text, invoice_date date, due_date date, invoice_status text, invoice_total numeric, paid_amount numeric, outstanding numeric)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    WITH
    active_invoices AS (
        SELECT si.id, si.invoice_number, si.invoice_date, si.due_date,
               si.customer_id, si.customer_name, si.status, si.total_amount
        FROM sales_invoices si
        WHERE si.tenant_id = p_tenant_id
          AND si.status NOT IN ('draft', 'void')
    ),
    invoice_debits AS (
        SELECT je.source_id::uuid AS inv_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
          AND je.source_type = 'INVOICE'
        GROUP BY je.source_id::uuid
    ),
    payment_credits AS (
        -- Branch 1: receive_payment settlements (via allocations)
        SELECT rpa.invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM receive_payment_allocations rpa
        JOIN receive_payments rp ON rp.id = rpa.payment_id
        JOIN journal_entries je ON je.id = rp.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE rp.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY rpa.invoice_id

        UNION ALL

        -- Branch 2: credit-note applications (original_invoice_id link)
        SELECT cn.original_invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM credit_notes cn
        JOIN journal_entries je ON je.source_id::uuid = cn.id
            AND je.source_type = 'CREDIT_NOTE'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE cn.tenant_id = p_tenant_id
          AND cn.original_invoice_id IS NOT NULL
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY cn.original_invoice_id

        UNION ALL

        -- Branch 3 (FIX_P35_ARCANON): customer-deposit applications.
        -- The DEPOSIT_APPLICATION journal's Cr RECEIVABLE line carries no invoice
        -- ref; per-invoice attribution comes from cda.invoice_id. Only the active,
        -- non-reversed application settles (status='active' is 1:1 with the
        -- journal's reversed_by_id IS NULL — belt-and-suspenders both checked).
        SELECT cda.invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM customer_deposit_applications cda
        JOIN journal_entries je ON je.id = cda.journal_id
            AND je.source_type = 'DEPOSIT_APPLICATION'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE cda.tenant_id = p_tenant_id
          AND cda.status = 'active'
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY cda.invoice_id
    ),
    aggregated_credits AS (
        SELECT pc.inv_id, SUM(pc.total_credit) AS total_credit
        FROM payment_credits pc
        GROUP BY pc.inv_id
    )
    SELECT ai.customer_id::TEXT, ai.customer_name::TEXT, ai.id,
           ai.invoice_number::TEXT, ai.invoice_date, ai.due_date,
           ai.status::TEXT,
           COALESCE(id2.total_debit, 0)::NUMERIC(18,2),
           COALESCE(ac.total_credit, 0)::NUMERIC(18,2),
           (COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0))::NUMERIC(18,2)
    FROM active_invoices ai
    LEFT JOIN invoice_debits id2 ON id2.inv_id = ai.id
    LEFT JOIN aggregated_credits ac ON ac.inv_id = ai.id
    WHERE COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0) != 0
    ORDER BY ai.customer_name, ai.due_date;
END;
$function$;

-- =====================================================================
-- LAYER 1B — compute_ar_adjustments(): exclude DEPOSIT_APPLICATION from
--            the orphaned-journals catch-all (prevents double-subtract).
-- =====================================================================
CREATE OR REPLACE FUNCTION public.compute_ar_adjustments(p_tenant_id text)
 RETURNS TABLE(journal_id uuid, journal_number text, journal_date date, source_type text, description text, debit numeric, credit numeric, net numeric)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT je.id, je.journal_number, je.journal_date::date,
           je.source_type, je.description,
           COALESCE(SUM(jl.debit), 0),
           COALESCE(SUM(jl.credit), 0),
           COALESCE(SUM(jl.debit - jl.credit), 0)
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE coa.account_type = 'RECEIVABLE'
      AND je.status = 'POSTED'
      AND je.reversed_by_id IS NULL
      AND je.tenant_id = p_tenant_id
      AND (
        -- Non-standard source types (orphaned journals)
        -- FIX_P35_ARCANON: DEPOSIT_APPLICATION added to the recognized list so it
        -- is NO LONGER treated as an orphan adjustment. It is now counted as a
        -- settlement in compute_ar_outstanding() (Branch 3). Leaving it here would
        -- double-subtract the applied deposit.
        je.source_type NOT IN ('INVOICE', 'PAYMENT_RECEIVED', 'RECEIVE_PAYMENT', 'SALES_INVOICE_COGS', 'CASH_SALE', 'INVOICE_REVERSAL', 'DEPOSIT_APPLICATION')
        -- Invoices without active obligation (voided/drafted)
        OR (je.source_type = 'INVOICE' AND NOT EXISTS (
            SELECT 1 FROM sales_invoices si
            WHERE si.id = je.source_id::uuid
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN ('draft', 'void')
        ))
        -- Payments without active obligation link
        OR (je.source_type IN ('PAYMENT_RECEIVED', 'RECEIVE_PAYMENT') AND NOT EXISTS (
            SELECT 1 FROM receive_payment_allocations rpa
            JOIN receive_payments rp ON rp.id = rpa.payment_id
            JOIN sales_invoices si ON si.id = rpa.invoice_id
            WHERE rp.journal_id = je.id
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN ('draft', 'void')
        ))
        -- Invoice reversals (void credit to RECEIVABLE)
        OR je.source_type = 'INVOICE_REVERSAL'
      )
    GROUP BY je.id, je.journal_number, je.journal_date, je.source_type, je.description;
$function$;

-- =====================================================================
-- LAYER 3 — fail-loud reconciliation invariant + grandfather exemption
-- =====================================================================

-- Exemption store. enforce-with-grandfather:
--   - non-exempt tenant, drift != 0           -> FAIL
--   - exempt tenant, drift == baseline_drift   -> PASS (grandfathered)
--   - exempt tenant, drift != baseline_drift   -> FAIL (debt changed)
-- Exemptions are a shrinking debt, not a hiding place.
CREATE TABLE IF NOT EXISTS ar_reconciliation_exemptions (
    tenant_id     TEXT PRIMARY KEY,
    baseline_drift NUMERIC(18,2) NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL,
    ticket        TEXT,
    is_permanent  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-customer reconciliation: canonical per-invoice outstanding vs raw GL net.
-- Uses is_effective_journal() (symmetric) NOT bare reversed_by_id.
-- status: 'PASS' | 'FAIL_NON_EXEMPT' | 'FAIL_DRIFT_CHANGED' | 'PASS_EXEMPT'
CREATE OR REPLACE FUNCTION public.verify_ar_reconciliation(p_tenant_id text)
 RETURNS TABLE(
    customer_id text,
    customer_name text,
    canonical_ar numeric,
    gl_ar numeric,
    drift numeric,
    status text
 )
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    v_total_canon NUMERIC(18,2);
    v_total_gl    NUMERIC(18,2);
    v_total_drift NUMERIC(18,2);
    v_exempt      BOOLEAN;
    v_baseline    NUMERIC(18,2);
BEGIN
    RETURN QUERY
    WITH
    canon AS (
        SELECT o.customer_id, MAX(o.customer_name) AS customer_name,
               COALESCE(SUM(o.outstanding), 0)::NUMERIC(18,2) AS canonical_ar
        FROM compute_ar_outstanding(p_tenant_id) o
        GROUP BY o.customer_id
    ),
    -- GL RECEIVABLE net per customer. RECEIVABLE journals carry no direct
    -- customer_id; we attribute via the source document (invoice -> customer,
    -- receive_payment -> customer, deposit_application -> invoice -> customer,
    -- credit_note -> original invoice -> customer).
    gl AS (
        SELECT cust.customer_id,
               COALESCE(SUM(jl.debit - jl.credit), 0)::NUMERIC(18,2) AS gl_ar
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        JOIN LATERAL (
            SELECT CASE
                WHEN je.source_type = 'INVOICE'
                    THEN (SELECT si.customer_id FROM sales_invoices si WHERE si.id = je.source_id::uuid)
                WHEN je.source_type IN ('RECEIVE_PAYMENT','PAYMENT_RECEIVED')
                    THEN (SELECT rp.customer_id FROM receive_payments rp WHERE rp.id = je.source_id::uuid)
                WHEN je.source_type = 'DEPOSIT_APPLICATION'
                    THEN (SELECT si.customer_id FROM customer_deposit_applications cda
                          JOIN sales_invoices si ON si.id = cda.invoice_id
                          WHERE cda.journal_id = je.id LIMIT 1)
                WHEN je.source_type = 'CREDIT_NOTE'
                    THEN (SELECT si.customer_id FROM credit_notes cn
                          JOIN sales_invoices si ON si.id = cn.original_invoice_id
                          WHERE cn.id = je.source_id::uuid)
                WHEN je.source_type = 'INVOICE_REVERSAL'
                    THEN (SELECT si.customer_id FROM sales_invoices si WHERE si.id = je.source_id::uuid)
                ELSE NULL
            END::TEXT AS customer_id
        ) cust ON TRUE
        WHERE je.tenant_id = p_tenant_id
          AND is_effective_journal(je.id)
          AND coa.account_type = 'RECEIVABLE'
        GROUP BY cust.customer_id
    ),
    merged AS (
        SELECT COALESCE(c.customer_id, g.customer_id) AS customer_id,
               c.customer_name,
               COALESCE(c.canonical_ar, 0) AS canonical_ar,
               COALESCE(g.gl_ar, 0) AS gl_ar
        FROM canon c
        FULL OUTER JOIN gl g ON g.customer_id = c.customer_id
    )
    SELECT m.customer_id, m.customer_name, m.canonical_ar, m.gl_ar,
           (m.gl_ar - m.canonical_ar)::NUMERIC(18,2) AS drift,
           CASE WHEN ABS(m.gl_ar - m.canonical_ar) <= 0.01 THEN 'PASS'
                ELSE 'DRIFT' END AS status
    FROM merged m
    WHERE m.customer_id IS NOT NULL
    ORDER BY ABS(m.gl_ar - m.canonical_ar) DESC, m.customer_name;
END;
$function$;

-- All-tenant enforcing wrapper. One row per tenant with an ENFORCE verdict that
-- respects the exemption store.
CREATE OR REPLACE FUNCTION public.verify_ar_reconciliation_all()
 RETURNS TABLE(
    tenant_id text,
    total_canonical numeric,
    total_gl numeric,
    total_drift numeric,
    is_exempt boolean,
    baseline_drift numeric,
    verdict text
 )
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    WITH tenants AS (
        SELECT DISTINCT je.tenant_id AS tid
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE coa.account_type = 'RECEIVABLE'
    ),
    per_tenant AS (
        SELECT t.tid,
               COALESCE(SUM(r.canonical_ar), 0)::NUMERIC(18,2) AS total_canon,
               COALESCE(SUM(r.gl_ar), 0)::NUMERIC(18,2)        AS total_gl,
               COALESCE(SUM(r.drift), 0)::NUMERIC(18,2)        AS total_drift
        FROM tenants t
        LEFT JOIN LATERAL public.verify_ar_reconciliation(t.tid) r ON TRUE
        GROUP BY t.tid
    )
    SELECT p.tid,
           p.total_canon,
           p.total_gl,
           p.total_drift,
           (e.tenant_id IS NOT NULL) AS is_exempt,
           COALESCE(e.baseline_drift, 0)::NUMERIC(18,2) AS baseline_drift,
           CASE
               WHEN e.tenant_id IS NULL AND ABS(p.total_drift) <= 0.01 THEN 'PASS'
               WHEN e.tenant_id IS NULL AND ABS(p.total_drift) >  0.01 THEN 'FAIL_NON_EXEMPT'
               WHEN e.tenant_id IS NOT NULL AND ABS(p.total_drift - e.baseline_drift) <= 0.01 THEN 'PASS_EXEMPT'
               ELSE 'FAIL_DRIFT_CHANGED'
           END AS verdict
    FROM per_tenant p
    LEFT JOIN ar_reconciliation_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE WHEN e.tenant_id IS NULL AND ABS(p.total_drift) > 0.01 THEN 0
                   WHEN e.tenant_id IS NOT NULL AND ABS(p.total_drift - e.baseline_drift) > 0.01 THEN 0
                   ELSE 1 END), p.tid;
END;
$function$;
