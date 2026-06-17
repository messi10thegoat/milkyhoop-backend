-- V181 — Inventory WAC reconciliation fail-loud guard (health-check level).
--
-- Mirrors verify_ar_reconciliation_all() (Check 14 rigor): per-tenant compare of
-- the GL inventory CoA balance (journal-derived, POSTED, is_effective_journal so
-- symmetric reversals net out) vs the inventory_ledger value-on-hand computed as
-- Σ over products (current_on_hand_qty × current_WAC).
--
-- NOT a close-time hard gate — health-check (daily) only, to avoid over-reject.
-- Detects the WAC-inflation bug class (golden-apparel Kain) where average_cost
-- snapshots drift from the GL inventory asset value.
--
-- Tolerance: PRINCIPLED 2dp-rounding bound, NOT a flat constant. WAC is stored
-- at 2 decimals, so each product contributes at most (on_hand_qty × 0.005) of
-- pure-rounding drift between the full-precision GL value and the 2dp WAC value.
-- Per tenant:  tolerance = Σ (ABS(on_hand_qty) × 0.005) over products + 0.01 ε.
-- This tolerates EXACTLY the unavoidable rounding and no more: the WAC-inflation
-- bug class (thousands→millions, 13×–6 orders of magnitude) stays far outside
-- and is still flagged FAIL_NON_EXEMPT. (A flat 0.01 cried wolf on every healthy
-- new tenant holding products with non-terminating WAC, e.g. 17142.857…)
-- Exempt tenants keep their pinned baselines (baseline-change ε stays 0.01).
--
-- Iron Laws: read-only (STABLE), journal-derived (Law 1/16), Decimal precision
-- via NUMERIC(18,2) (Law 25). Inventory CoA resolved via account_roles role_key
-- 'INVENTORY_MERCHANDISE' with account_code '1-10600' fallback (health-check SQL
-- is permitted to reference account_code per milkyhoop-inventory Rule 6).

CREATE TABLE IF NOT EXISTS inventory_wac_reconciliation_exemptions (
    tenant_id      text PRIMARY KEY,
    baseline_drift numeric(18,2) NOT NULL DEFAULT 0,
    reason         text NOT NULL,
    ticket         text,
    is_permanent   boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Per-tenant inventory WAC reconciliation.
CREATE OR REPLACE FUNCTION public.verify_inventory_wac_reconciliation_all()
RETURNS TABLE(
    tenant_id  text,
    gl_value   numeric,
    wac_value  numeric,
    drift      numeric,
    tolerance  numeric,
    is_exempt  boolean,
    verdict    text
)
LANGUAGE plpgsql
STABLE
AS $function$
BEGIN
    RETURN QUERY
    WITH tenants AS (
        -- Any tenant that has touched the inventory asset CoA OR has ledger rows.
        SELECT DISTINCT il.tenant_id AS tid
        FROM inventory_ledger il
        UNION
        SELECT DISTINCT je.tenant_id
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE coa.account_code = '1-10600'
    ),
    -- GL inventory asset balance (journal-derived, effective only).
    -- Inventory CoA resolved per tenant via role_key, fallback to 1-10600.
    gl AS (
        SELECT t.tid,
               COALESCE(SUM(jl.debit - jl.credit), 0)::numeric(18,2) AS gl_value
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
        GROUP BY t.tid
    ),
    -- inventory_ledger value-on-hand = Σ over products
    --   ( current_on_hand_qty × current_WAC )
    -- current_on_hand_qty = SUM(quantity_in) - SUM(quantity_out)
    -- current_WAC = average_cost of the latest movement (chronological) per product
    last_mv AS (
        SELECT DISTINCT ON (il.tenant_id, il.product_id)
               il.tenant_id, il.product_id, il.average_cost
        FROM inventory_ledger il
        ORDER BY il.tenant_id, il.product_id,
                 il.movement_date DESC, il.created_at DESC, il.id DESC
    ),
    onhand AS (
        SELECT il.tenant_id, il.product_id,
               COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) AS qty
        FROM inventory_ledger il
        GROUP BY il.tenant_id, il.product_id
    ),
    wac AS (
        SELECT o.tenant_id AS tid,
               COALESCE(SUM(o.qty * lm.average_cost), 0)::numeric(18,2) AS wac_value
        FROM onhand o
        JOIN last_mv lm
              ON lm.tenant_id = o.tenant_id
             AND lm.product_id = o.product_id
        GROUP BY o.tenant_id
    ),
    -- PRINCIPLED rounding tolerance: each product's 2dp WAC rounding can be off by
    -- at most 0.005 per on-hand unit, so the per-tenant rounding bound is
    --   Σ ( ABS(on_hand_qty) × 0.005 ).  ABS guards negative-on-hand edge rows.
    -- A small fixed epsilon (0.01) absorbs the GL/wac numeric(18,2) casts.
    tol AS (
        SELECT o.tenant_id AS tid,
               (COALESCE(SUM(ABS(o.qty) * 0.005), 0) + 0.01)::numeric AS tolerance
        FROM onhand o
        GROUP BY o.tenant_id
    ),
    per_tenant AS (
        SELECT t.tid,
               COALESCE(g.gl_value, 0)::numeric(18,2)  AS gl_value,
               COALESCE(w.wac_value, 0)::numeric(18,2) AS wac_value,
               (COALESCE(g.gl_value, 0) - COALESCE(w.wac_value, 0))::numeric(18,2) AS drift,
               COALESCE(tl.tolerance, 0.01)::numeric    AS tolerance
        FROM tenants t
        LEFT JOIN gl g  ON g.tid = t.tid
        LEFT JOIN wac w ON w.tid = t.tid
        LEFT JOIN tol tl ON tl.tid = t.tid
    )
    SELECT p.tid,
           p.gl_value,
           p.wac_value,
           p.drift,
           p.tolerance,
           (e.tenant_id IS NOT NULL) AS is_exempt,
           CASE
               WHEN e.tenant_id IS NULL AND ABS(p.drift) <= p.tolerance THEN 'PASS'
               WHEN e.tenant_id IS NULL AND ABS(p.drift) >  p.tolerance THEN 'FAIL_NON_EXEMPT'
               WHEN e.tenant_id IS NOT NULL
                    AND ABS(p.drift - e.baseline_drift) <= 0.01 THEN 'PASS_EXEMPT'
               ELSE 'FAIL_DRIFT_CHANGED'
           END AS verdict
    FROM per_tenant p
    LEFT JOIN inventory_wac_reconciliation_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE
                WHEN e.tenant_id IS NULL AND ABS(p.drift) > p.tolerance THEN 0
                WHEN e.tenant_id IS NOT NULL
                     AND ABS(p.drift - e.baseline_drift) > 0.01 THEN 0
                ELSE 1 END), p.tid;
END;
$function$;
