-- V188__r9_guards_fail_closed.sql
--
-- Make the 5 BankSync / accounting reconciliation guard functions FAIL-CLOSED.
--
-- PROBLEM (false-GREEN, masked a live R9 violation for months):
--   All 5 guards are LANGUAGE plpgsql, NOT SECURITY DEFINER, owned by postgres,
--   and scope a tenant by reading RLS-bound tables (bank_accounts, journal_entries,
--   sales_invoices, inventory_ledger, ...).  A NOBYPASSRLS caller (e.g. milkyadmin)
--   WITHOUT app.tenant_id set -> RLS hides every row -> the guard returns 0 rows ->
--   the caller reads that as a silent PASS.  A real BankSync Rule-9 gap went
--   undetected for months because of exactly this.
--
-- FIX:
--   Insert a fail-closed gate at the top of each function body that RAISES only in
--   the genuine false-GREEN scenario:
--     caller is NOT BYPASSRLS  AND  app.tenant_id is unset  AND  (no tenant arg).
--   The rolbypassrls check is the load-bearing part: it lets the daily health cron
--   (which runs as postgres / BYPASSRLS, context-less, scanning ALL tenants) keep
--   working untouched, while killing the false-GREEN for a NOBYPASSRLS caller that
--   forgot to scope.
--
--   For the 2 arg-taking functions the argument is also made authoritative: when
--   p_tenant_id is supplied we set_config('app.tenant_id', p_tenant_id, true)
--   (txn-scoped) BEFORE the body, so an arg-only NOBYPASSRLS call establishes its
--   own RLS context and the gate does not fire.
--
-- NET BEHAVIOR:
--   * postgres / BYPASSRLS, context-less (the cron) -> NO raise, scans all tenants.
--   * NOBYPASSRLS + app.tenant_id set (or arg given) -> works, same result as before.
--   * NOBYPASSRLS + no context + no arg            -> RAISES (false-GREEN killed).
--
-- The function bodies below are preserved BYTE-FOR-BYTE from the live definitions
-- (dumped via pg_get_functiondef); the ONLY change is the guard block inserted
-- immediately after BEGIN (and, for the 2 arg-taking fns, the set_config call).
-- Signatures, return types, language, volatility and ownership are unchanged.
--
-- Author: r9-guards-fail-closed
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1) check_bank_sync_health(p_tenant_id text DEFAULT NULL)  [arg-taking]
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.check_bank_sync_health(p_tenant_id text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id text, bank_name text, journal_balance numeric, txn_balance numeric, gap numeric, orphan_journals bigint, orphan_bank_txns bigint)
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- FAIL-CLOSED gate (V188): refuse to return a false-GREEN.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
       AND (p_tenant_id IS NULL OR p_tenant_id = '')
    THEN
        RAISE EXCEPTION 'check_bank_sync_health: no tenant scope (not BYPASSRLS, app.tenant_id unset, no arg) -- refusing to return false-GREEN';
    END IF;
    -- If an arg was supplied, make it authoritative for RLS (txn-scoped).
    IF p_tenant_id IS NOT NULL AND p_tenant_id <> '' THEN
        PERFORM set_config('app.tenant_id', p_tenant_id, true);
    END IF;

    RETURN QUERY
    WITH bank_coa AS (
        SELECT ba.id as bank_account_id, ba.account_name::TEXT as bank_name, ba.coa_id,
               ba.tenant_id
        FROM bank_accounts ba
        WHERE ba.coa_id IS NOT NULL
          AND (p_tenant_id IS NULL OR ba.tenant_id = p_tenant_id)
    ),
    j_bal AS (
        SELECT bc.bank_account_id, bc.bank_name, bc.tenant_id,
               COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)::NUMERIC as journal_balance
        FROM bank_coa bc
        LEFT JOIN journal_lines jl ON jl.account_id = bc.coa_id
        LEFT JOIN journal_entries je ON je.id = jl.journal_id AND je.status = 'POSTED'
        GROUP BY bc.bank_account_id, bc.bank_name, bc.tenant_id
    ),
    t_bal AS (
        SELECT bt.bank_account_id,
               COALESCE(SUM(bt.amount), 0)::NUMERIC as txn_balance
        FROM bank_transactions bt
        GROUP BY bt.bank_account_id
    ),
    orphan_j AS (
        SELECT bc.bank_account_id,
               COUNT(DISTINCT je.id) as cnt
        FROM bank_coa bc
        JOIN journal_lines jl ON jl.account_id = bc.coa_id
        JOIN journal_entries je ON je.id = jl.journal_id AND je.status = 'POSTED'
        LEFT JOIN bank_transactions bt ON bt.journal_id = je.id
        WHERE bt.id IS NULL
        GROUP BY bc.bank_account_id
    ),
    orphan_bt AS (
        SELECT bt.bank_account_id,
               COUNT(*) as cnt
        FROM bank_transactions bt
        WHERE bt.journal_id IS NULL
        GROUP BY bt.bank_account_id
    )
    SELECT
        j.tenant_id,
        j.bank_name,
        j.journal_balance,
        COALESCE(t.txn_balance, 0::NUMERIC),
        (j.journal_balance - COALESCE(t.txn_balance, 0::NUMERIC))::NUMERIC,
        COALESCE(oj.cnt, 0::BIGINT),
        COALESCE(ob.cnt, 0::BIGINT)
    FROM j_bal j
    LEFT JOIN t_bal t ON t.bank_account_id = j.bank_account_id
    LEFT JOIN orphan_j oj ON oj.bank_account_id = j.bank_account_id
    LEFT JOIN orphan_bt ob ON ob.bank_account_id = j.bank_account_id
    ORDER BY ABS(j.journal_balance - COALESCE(t.txn_balance, 0::NUMERIC)) DESC;
END;
$function$;

-- ---------------------------------------------------------------------------
-- 2) verify_chain_integrity(p_tenant_id text)  [arg-taking]
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.verify_chain_integrity(p_tenant_id text)
 RETURNS TABLE(journal_id uuid, chain_sequence bigint, stored_hash character varying, computed_hash character varying, is_valid boolean)
 LANGUAGE plpgsql
AS $function$
    DECLARE
        v_entry RECORD;
        v_prev_hash VARCHAR := 'GENESIS';
        v_computed VARCHAR;
    BEGIN
        -- FAIL-CLOSED gate (V188): refuse to return a false-GREEN.
        IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
           AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
           AND (p_tenant_id IS NULL OR p_tenant_id = '')
        THEN
            RAISE EXCEPTION 'verify_chain_integrity: no tenant scope (not BYPASSRLS, app.tenant_id unset, no arg) -- refusing to return false-GREEN';
        END IF;
        -- If an arg was supplied, make it authoritative for RLS (txn-scoped).
        IF p_tenant_id IS NOT NULL AND p_tenant_id <> '' THEN
            PERFORM set_config('app.tenant_id', p_tenant_id, true);
        END IF;

        FOR v_entry IN
            SELECT je.id, je.chain_sequence, je.content_hash, je.previous_hash
            FROM journal_entries je
            WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED'
            ORDER BY je.chain_sequence ASC
        LOOP
            v_computed := compute_journal_hash(v_entry.id, v_prev_hash);

            RETURN QUERY SELECT
                v_entry.id,
                v_entry.chain_sequence,
                v_entry.content_hash,
                v_computed,
                (v_entry.content_hash = v_computed);

            v_prev_hash := v_entry.content_hash;
        END LOOP;
    END;
    $function$;

-- ---------------------------------------------------------------------------
-- 3) verify_ar_reconciliation_all()  [no-arg]
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.verify_ar_reconciliation_all()
 RETURNS TABLE(tenant_id text, total_canonical numeric, total_gl numeric, total_drift numeric, is_exempt boolean, baseline_drift numeric, verdict text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    -- FAIL-CLOSED gate (V188): refuse to return a false-GREEN.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_ar_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

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

-- ---------------------------------------------------------------------------
-- 4) verify_inventory_wac_reconciliation_all()  [no-arg]
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.verify_inventory_wac_reconciliation_all()
 RETURNS TABLE(tenant_id text, gl_value numeric, wac_value numeric, drift numeric, tolerance numeric, is_exempt boolean, verdict text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    -- FAIL-CLOSED gate (V188): refuse to return a false-GREEN.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_inventory_wac_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

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
    -- current_WAC = average_cost of the last-APPLIED movement (application order: created_at, id) per product
    last_mv AS (
        SELECT DISTINCT ON (il.tenant_id, il.product_id)
               il.tenant_id, il.product_id, il.average_cost
        FROM inventory_ledger il
        ORDER BY il.tenant_id, il.product_id,
                 il.created_at DESC, il.id DESC
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

-- ---------------------------------------------------------------------------
-- 5) verify_deferred_revenue_reconciliation_all()  [no-arg]
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.verify_deferred_revenue_reconciliation_all()
 RETURNS TABLE(tenant_id text, canonical numeric, gl_value numeric, drift numeric, tolerance numeric, is_exempt boolean, verdict text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    -- FAIL-CLOSED gate (V188): refuse to return a false-GREEN.
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_deferred_revenue_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        -- Any tenant that has a REVENUE_DEFERRED role-mapped account OR has any
        -- non-fully-recognized posted invoice line.
        SELECT DISTINCT ar.tenant_id AS tid
        FROM account_roles ar
        WHERE ar.role_key = 'REVENUE_DEFERRED'
        UNION
        SELECT DISTINCT si.tenant_id
        FROM sales_invoices si
        WHERE si.status IN ('posted','paid','partial')
    ),
    -- Canonical: Σ (allocated - recognized) over effective posted invoices,
    -- plus the per-tenant count of invoices that carry any open deferral
    -- (used for the principled rounding tolerance).
    canon AS (
        SELECT si.tenant_id AS tid,
               COALESCE(SUM(sii.allocated_amount - sii.recognized_amount), 0)::numeric(18,2) AS canonical,
               COUNT(DISTINCT si.id) AS n_inv
        FROM sales_invoices si
        JOIN sales_invoice_items sii ON sii.invoice_id = si.id
        WHERE si.status IN ('posted','paid','partial')
        GROUP BY si.tenant_id
    ),
    -- GL net (Cr - Dr) of the per-tenant REVENUE_DEFERRED account,
    -- POSTED + is_effective journals only (symmetric reversals net out).
    gl AS (
        SELECT je.tenant_id AS tid,
               COALESCE(SUM(jl.credit - jl.debit), 0)::numeric(18,2) AS gl_value
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN account_roles ar
              ON ar.tenant_id = je.tenant_id
             AND ar.role_key = 'REVENUE_DEFERRED'
             AND ar.account_id = jl.account_id
        WHERE is_effective_journal(je.id)
        GROUP BY je.tenant_id
    ),
    per_tenant AS (
        SELECT t.tid,
               COALESCE(c.canonical, 0)::numeric(18,2) AS canonical,
               COALESCE(g.gl_value, 0)::numeric(18,2)  AS gl_value,
               (COALESCE(c.canonical, 0) - COALESCE(g.gl_value, 0))::numeric(18,2) AS drift,
               (COALESCE(c.n_inv, 0) * 0.01 + 0.01)::numeric AS tolerance
        FROM tenants t
        LEFT JOIN canon c ON c.tid = t.tid
        LEFT JOIN gl g    ON g.tid = t.tid
    )
    SELECT p.tid,
           p.canonical,
           p.gl_value,
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
    LEFT JOIN deferred_revenue_reconciliation_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE
                WHEN e.tenant_id IS NULL AND ABS(p.drift) > p.tolerance THEN 0
                WHEN e.tenant_id IS NOT NULL
                     AND ABS(p.drift - e.baseline_drift) > 0.01 THEN 0
                ELSE 1 END), p.tid;
END;
$function$;
