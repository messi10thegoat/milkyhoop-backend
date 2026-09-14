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
$function$

