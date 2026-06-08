-- Fase G-10 Step 5.1: get_revenue_by_basis() and get_expenses_by_basis()
-- must filter via is_effective_journal() so they don't double-count
-- orphan-reversal lines that survive the je.status='POSTED' check alone.
--
-- Root cause: P&L (and Dashboard summary.laba_rugi which calls these) used
-- only status='POSTED', missing the reversed_by_id / reversal_of_id filter
-- now centralized in is_effective_journal() (V169). Result: STALE P&L net
-- income (golden-apparel 10,997,001 vs GL truth 11,485,001, Δ=488k from
-- 100k retur + 588k CN_COGS orphan-reversal residue).
--
-- Fix: wrap journal_entries filter with is_effective_journal(je.id)=true.
-- Pattern matches existing reports/neraca path (uses is_effective_journal()
-- since T2.5 migration).

CREATE OR REPLACE FUNCTION get_revenue_by_basis(
    p_tenant_id text,
    p_start_date date,
    p_end_date date,
    p_basis character varying DEFAULT 'accrual'::character varying
) RETURNS TABLE(
    account_id uuid,
    account_code text,
    account_name text,
    total_amount bigint
) LANGUAGE plpgsql SECURITY DEFINER AS $function$
BEGIN
    PERFORM set_config('app.tenant_id', p_tenant_id, true);

    IF p_basis = 'cash' THEN
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.name,
            SUM(jl.credit - jl.debit)::BIGINT as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.journal_date BETWEEN p_start_date AND p_end_date
        AND je.source_type IN ('PAYMENT_RECEIPT', 'CASH_SALE')
        AND coa.account_type = 'REVENUE'
        AND je.status = 'POSTED'
        AND is_effective_journal(je.id) = true
        GROUP BY jl.account_id, coa.account_code, coa.name
        HAVING SUM(jl.credit - jl.debit) != 0;
    ELSE
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.name,
            SUM(jl.credit - jl.debit)::BIGINT as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.journal_date BETWEEN p_start_date AND p_end_date
        AND coa.account_type = 'REVENUE'
        AND je.status = 'POSTED'
        AND is_effective_journal(je.id) = true
        GROUP BY jl.account_id, coa.account_code, coa.name
        HAVING SUM(jl.credit - jl.debit) != 0;
    END IF;
END;
$function$;

CREATE OR REPLACE FUNCTION get_expenses_by_basis(
    p_tenant_id text,
    p_start_date date,
    p_end_date date,
    p_basis character varying DEFAULT 'accrual'::character varying
) RETURNS TABLE(
    account_id uuid,
    account_code text,
    account_name text,
    total_amount numeric
) LANGUAGE plpgsql SECURITY DEFINER AS $function$
BEGIN
    PERFORM set_config('app.tenant_id', p_tenant_id, true);

    IF p_basis = 'cash' THEN
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.name,
            SUM(jl.debit - jl.credit)::numeric as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.journal_date BETWEEN p_start_date AND p_end_date
        AND je.source_type IN ('PAYMENT_MADE', 'CASH_EXPENSE')
        AND coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
        AND je.status = 'POSTED'
        AND is_effective_journal(je.id) = true
        GROUP BY jl.account_id, coa.account_code, coa.name
        HAVING SUM(jl.debit - jl.credit) != 0;
    ELSE
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.name,
            SUM(jl.debit - jl.credit)::numeric as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.journal_date BETWEEN p_start_date AND p_end_date
        AND coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
        AND je.status = 'POSTED'
        AND is_effective_journal(je.id) = true
        GROUP BY jl.account_id, coa.account_code, coa.name
        HAVING SUM(jl.debit - jl.credit) != 0;
    END IF;
END;
$function$;

COMMENT ON FUNCTION get_revenue_by_basis(text, date, date, varchar) IS
'V170 Fase G-10 Step 5.1: filters via is_effective_journal() to exclude orphan-reversal entries. Used by /api/reports/profit-loss and /api/dashboard/summary laba_rugi.';

COMMENT ON FUNCTION get_expenses_by_basis(text, date, date, varchar) IS
'V170 Fase G-10 Step 5.1: filters via is_effective_journal() to exclude orphan-reversal entries. Used by /api/reports/profit-loss and /api/dashboard/summary laba_rugi.';
