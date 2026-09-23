-- V298: AR aging functions rebuilt on the LIVE journal-derived computation (compute_ar_outstanding,
-- Iron Law 1/16). The old bodies read sales_invoices.paid_amount -- a column that does not exist --
-- so GET /api/reports/ar-aging, /ar-aging/detail and /ar-aging/customer/{id} answered 500 on EVERY
-- call (measured 23 Sep 2026; the chat's "umur piutang" action calls /ar-aging). Even when the column
-- existed it was the cache, not the ledger.
-- Same result COLUMNS as before (the response contract is unchanged); money columns become
-- NUMERIC(18,2) (the old ::BIGINT dropped sen). Buckets = open invoices (invoice rows with
-- outstanding > 0), aged against p_as_of_date exactly as before: current (as_of <= due_date),
-- 1-30, 31-60, 61-90, 91-120, 120+. compute_ar_outstanding's synthetic unapplied-credit-note rows
-- (invoice_id NULL) are not invoices and are not bucketed.
-- Outstanding is TODAY's ledger balance: compute_ar_outstanding has no as-of date. The router refuses
-- a past as_of (422) rather than age today's balances as if they were historical.
-- get_ar_aging_customer now takes the tenant (it filtered by customer id only).

DROP FUNCTION IF EXISTS get_ar_aging_summary(text, date);
DROP FUNCTION IF EXISTS get_ar_aging_detail(text, date);
DROP FUNCTION IF EXISTS get_ar_aging_customer(uuid, date);

CREATE FUNCTION ar_aging_open_invoices(p_tenant_id text, p_as_of_date date)
 RETURNS TABLE(customer_id text, customer_name text, invoice_id uuid, invoice_number text, invoice_date date,
               due_date date, invoice_total numeric(18,2), paid_amount numeric(18,2), outstanding numeric(18,2),
               days_overdue integer, bucket text)
 LANGUAGE sql STABLE
AS $function$
    SELECT o.customer_id, o.customer_name, o.invoice_id, o.invoice_number, o.invoice_date, o.due_date,
           o.invoice_total::numeric(18,2), o.paid_amount::numeric(18,2), o.outstanding::numeric(18,2),
           GREATEST(0, p_as_of_date - o.due_date)::integer,
           CASE
               WHEN p_as_of_date <= o.due_date THEN 'current'
               WHEN (p_as_of_date - o.due_date) BETWEEN 1 AND 30 THEN '1-30 days'
               WHEN (p_as_of_date - o.due_date) BETWEEN 31 AND 60 THEN '31-60 days'
               WHEN (p_as_of_date - o.due_date) BETWEEN 61 AND 90 THEN '61-90 days'
               WHEN (p_as_of_date - o.due_date) BETWEEN 91 AND 120 THEN '91-120 days'
               ELSE '120+ days'
           END
    FROM compute_ar_outstanding(p_tenant_id) o
    WHERE o.invoice_id IS NOT NULL
      AND o.outstanding > 0
      AND o.invoice_date <= p_as_of_date
$function$;

CREATE FUNCTION get_ar_aging_summary(p_tenant_id text, p_as_of_date date DEFAULT CURRENT_DATE)
 RETURNS TABLE(total_current numeric(18,2), total_1_30 numeric(18,2), total_31_60 numeric(18,2),
               total_61_90 numeric(18,2), total_91_120 numeric(18,2), total_over_120 numeric(18,2),
               grand_total numeric(18,2), overdue_count bigint)
 LANGUAGE sql STABLE
AS $function$
    SELECT
        COALESCE(SUM(outstanding) FILTER (WHERE bucket = 'current'), 0)::numeric(18,2),
        COALESCE(SUM(outstanding) FILTER (WHERE bucket = '1-30 days'), 0)::numeric(18,2),
        COALESCE(SUM(outstanding) FILTER (WHERE bucket = '31-60 days'), 0)::numeric(18,2),
        COALESCE(SUM(outstanding) FILTER (WHERE bucket = '61-90 days'), 0)::numeric(18,2),
        COALESCE(SUM(outstanding) FILTER (WHERE bucket = '91-120 days'), 0)::numeric(18,2),
        COALESCE(SUM(outstanding) FILTER (WHERE bucket = '120+ days'), 0)::numeric(18,2),
        COALESCE(SUM(outstanding), 0)::numeric(18,2),
        COUNT(*) FILTER (WHERE bucket <> 'current')::bigint
    FROM ar_aging_open_invoices(p_tenant_id, p_as_of_date)
$function$;

CREATE FUNCTION get_ar_aging_detail(p_tenant_id text, p_as_of_date date DEFAULT CURRENT_DATE)
 RETURNS TABLE(customer_id uuid, customer_name character varying, customer_code character varying,
               current_amount numeric(18,2), days_1_30 numeric(18,2), days_31_60 numeric(18,2),
               days_61_90 numeric(18,2), days_91_120 numeric(18,2), days_over_120 numeric(18,2),
               total_balance numeric(18,2), oldest_invoice_date date, invoice_count bigint)
 LANGUAGE sql STABLE
AS $function$
    SELECT
        a.customer_id::uuid,
        MAX(a.customer_name)::character varying,
        MAX(c.code)::character varying,
        COALESCE(SUM(a.outstanding) FILTER (WHERE a.bucket = 'current'), 0)::numeric(18,2),
        COALESCE(SUM(a.outstanding) FILTER (WHERE a.bucket = '1-30 days'), 0)::numeric(18,2),
        COALESCE(SUM(a.outstanding) FILTER (WHERE a.bucket = '31-60 days'), 0)::numeric(18,2),
        COALESCE(SUM(a.outstanding) FILTER (WHERE a.bucket = '61-90 days'), 0)::numeric(18,2),
        COALESCE(SUM(a.outstanding) FILTER (WHERE a.bucket = '91-120 days'), 0)::numeric(18,2),
        COALESCE(SUM(a.outstanding) FILTER (WHERE a.bucket = '120+ days'), 0)::numeric(18,2),
        SUM(a.outstanding)::numeric(18,2),
        MIN(a.invoice_date),
        COUNT(*)::bigint
    FROM ar_aging_open_invoices(p_tenant_id, p_as_of_date) a
    LEFT JOIN customers c ON c.id::text = a.customer_id AND c.tenant_id = p_tenant_id
    WHERE a.customer_id IS NOT NULL  -- as before (inner join to customers): per-customer rows only
    GROUP BY a.customer_id
    ORDER BY SUM(a.outstanding) DESC
$function$;

CREATE FUNCTION get_ar_aging_customer(p_tenant_id text, p_customer_id uuid, p_as_of_date date DEFAULT CURRENT_DATE)
 RETURNS TABLE(invoice_id uuid, invoice_number character varying, invoice_date date, due_date date,
               total_amount numeric(18,2), paid_amount numeric(18,2), balance numeric(18,2),
               days_overdue integer, aging_bucket character varying)
 LANGUAGE sql STABLE
AS $function$
    SELECT a.invoice_id, a.invoice_number::character varying, a.invoice_date, a.due_date,
           a.invoice_total, a.paid_amount, a.outstanding, a.days_overdue, a.bucket::character varying
    FROM ar_aging_open_invoices(p_tenant_id, p_as_of_date) a
    WHERE a.customer_id = p_customer_id::text
    ORDER BY a.due_date ASC
$function$;
