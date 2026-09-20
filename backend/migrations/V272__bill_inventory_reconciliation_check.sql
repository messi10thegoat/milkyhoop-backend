-- V272__bill_inventory_reconciliation_check.sql
-- E1 (MASTER open list): Law-13-style dual-ledger checker for bill inventory.
-- Invariant per tenant: the NET Persediaan (1-10600 / INVENTORY_MERCHANDISE role) value
-- attributable to purchase bills == the inventory_ledger value booked from bills.
--   GL side  = SUM(debit - credit) on the inventory account over POSTED+effective
--              journals of source_type BILL or RECLASSIFY_BILL_INVENTORY (so a reclass
--              that moved a mis-posted non-inventory debit OUT to HPP nets to zero).
--   Ledger   = SUM(inventory_ledger.total_cost) WHERE source_type='BILL'.
-- A non-inventory bill that debits Persediaan WITHOUT recording an inventory_ledger
-- inbound (the dual-ledger break) makes GL > ledger -> drift -> RED.
-- Exemption store (mirrors inventory_wac_reconciliation_exemptions) grandfathers the
-- known frozen kaos drift; a drift CHANGE still fails loud.

CREATE TABLE IF NOT EXISTS bill_inventory_reconciliation_exemptions (
    tenant_id      text PRIMARY KEY,
    baseline_drift numeric NOT NULL,
    reason         text,
    ticket         text,
    is_permanent   boolean DEFAULT false,
    created_at     timestamptz DEFAULT now()
);

INSERT INTO bill_inventory_reconciliation_exemptions
    (tenant_id, baseline_drift, reason, ticket, is_permanent)
VALUES
    ('kaos-biru-konveksi', 80006.00,
     'Known unreclassified non-inventory bill Persediaan debits (test tenant). Frozen baseline; fails loud if it moves.',
     'E1-law13-bill-inventory-checker', false)
ON CONFLICT (tenant_id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.verify_bill_inventory_reconciliation_all()
 RETURNS TABLE(tenant_id text, gl_bill numeric, ledger_bill numeric, drift numeric, tolerance numeric, is_exempt boolean, verdict text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    -- FAIL-CLOSED gate (mirror V188): refuse to return a false-GREEN.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_bill_inventory_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT DISTINCT il.tenant_id AS tid FROM inventory_ledger il WHERE il.source_type = 'BILL'
        UNION
        SELECT DISTINCT je.tenant_id
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.source_type IN ('BILL', 'RECLASSIFY_BILL_INVENTORY')
          AND coa.account_code = '1-10600'
    ),
    -- GL net Persediaan attributable to bills (+ reclass credits net it back out).
    gl AS (
        SELECT t.tid,
               COALESCE(SUM(jl.debit - jl.credit), 0)::numeric(18,2) AS gl_bill
        FROM tenants t
        JOIN chart_of_accounts coa
              ON coa.tenant_id = t.tid
             AND ( coa.id = (SELECT ar.account_id FROM account_roles ar
                              WHERE ar.tenant_id = t.tid
                                AND ar.role_key = 'INVENTORY_MERCHANDISE')
                   OR ( NOT EXISTS (SELECT 1 FROM account_roles ar
                                    WHERE ar.tenant_id = t.tid
                                      AND ar.role_key = 'INVENTORY_MERCHANDISE')
                        AND coa.account_code = '1-10600' ) )
        JOIN journal_lines jl ON jl.account_id = coa.id
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.tenant_id = t.tid
          AND is_effective_journal(je.id)
          AND je.source_type IN ('BILL', 'RECLASSIFY_BILL_INVENTORY')
        GROUP BY t.tid
    ),
    led AS (
        SELECT t.tid,
               COALESCE(SUM(il.total_cost), 0)::numeric(18,2) AS ledger_bill
        FROM tenants t
        LEFT JOIN inventory_ledger il ON il.tenant_id = t.tid AND il.source_type = 'BILL'
        GROUP BY t.tid
    ),
    per_tenant AS (
        SELECT t.tid,
               COALESCE(g.gl_bill, 0)::numeric(18,2)  AS gl_bill,
               COALESCE(l.ledger_bill, 0)::numeric(18,2) AS ledger_bill,
               (COALESCE(g.gl_bill, 0) - COALESCE(l.ledger_bill, 0))::numeric(18,2) AS drift,
               1.00::numeric AS tolerance
        FROM tenants t
        LEFT JOIN gl g ON g.tid = t.tid
        LEFT JOIN led l ON l.tid = t.tid
    )
    SELECT p.tid, p.gl_bill, p.ledger_bill, p.drift, p.tolerance,
           (e.tenant_id IS NOT NULL) AS is_exempt,
           CASE
               WHEN e.tenant_id IS NULL AND ABS(p.drift) <= p.tolerance THEN 'PASS'
               WHEN e.tenant_id IS NULL AND ABS(p.drift) >  p.tolerance THEN 'FAIL_NON_EXEMPT'
               WHEN e.tenant_id IS NOT NULL
                    AND ABS(p.drift - e.baseline_drift) <= 0.01 THEN 'PASS_EXEMPT'
               ELSE 'FAIL_DRIFT_CHANGED'
           END AS verdict
    FROM per_tenant p
    LEFT JOIN bill_inventory_reconciliation_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE
                WHEN e.tenant_id IS NULL AND ABS(p.drift) > p.tolerance THEN 0
                WHEN e.tenant_id IS NOT NULL
                     AND ABS(p.drift - e.baseline_drift) > 0.01 THEN 0
                ELSE 1 END), p.tid;
END;
$function$;
