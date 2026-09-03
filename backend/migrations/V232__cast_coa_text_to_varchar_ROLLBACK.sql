-- ROLLBACK V232: kembalikan dua fungsi ke definisi V231 (tanpa cast).
--
-- ⚠️ Versi di bawah TIDAK BISA DIJALANKAN tanpa galat: ia mengembalikan
-- kolom `text` ke slot `character varying`, sehingga kedua fungsi kembali
-- melempar `structure of query does not match function result type`.
-- Itu memang arti rollback di sini. `entry_date` tetap `journal_date` —
-- rollback ini TIDAK mengembalikan V231.

-- ── get_budget_vs_actual (versi V231, tanpa cast) ──
CREATE OR REPLACE FUNCTION public.get_budget_vs_actual(p_budget_id uuid, p_month integer DEFAULT NULL::integer)
 RETURNS TABLE(account_id uuid, account_code character varying, account_name character varying, account_type character varying, cost_center_id uuid, cost_center_name character varying, budget_amount bigint, actual_amount bigint, variance bigint, percentage_used numeric)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    v_fiscal_year INTEGER;
    v_start_date DATE;
    v_end_date DATE;
BEGIN
    -- Get fiscal year
    SELECT fiscal_year INTO v_fiscal_year FROM budgets WHERE id = p_budget_id;

    -- Calculate date range
    IF p_month IS NULL THEN
        v_start_date := make_date(v_fiscal_year, 1, 1);
        v_end_date := make_date(v_fiscal_year, 12, 31);
    ELSE
        v_start_date := make_date(v_fiscal_year, p_month, 1);
        v_end_date := (v_start_date + INTERVAL '1 month' - INTERVAL '1 day')::DATE;
    END IF;

    RETURN QUERY
    WITH budget_data AS (
        SELECT
            bi.account_id,
            coa.account_code,
            coa.name as account_name,
            coa.account_type,
            bi.cost_center_id,
            cc.name as cost_center_name,
            CASE
                WHEN p_month IS NULL THEN bi.annual_amount
                WHEN p_month = 1 THEN bi.jan_amount
                WHEN p_month = 2 THEN bi.feb_amount
                WHEN p_month = 3 THEN bi.mar_amount
                WHEN p_month = 4 THEN bi.apr_amount
                WHEN p_month = 5 THEN bi.may_amount
                WHEN p_month = 6 THEN bi.jun_amount
                WHEN p_month = 7 THEN bi.jul_amount
                WHEN p_month = 8 THEN bi.aug_amount
                WHEN p_month = 9 THEN bi.sep_amount
                WHEN p_month = 10 THEN bi.oct_amount
                WHEN p_month = 11 THEN bi.nov_amount
                WHEN p_month = 12 THEN bi.dec_amount
                ELSE 0
            END as budget_amt
        FROM budget_items bi
        JOIN chart_of_accounts coa ON bi.account_id = coa.id
        LEFT JOIN cost_centers cc ON bi.cost_center_id = cc.id
        WHERE bi.budget_id = p_budget_id
    ),
    actual_data AS (
        SELECT
            jl.account_id,
            jl.cost_center_id,
            -- For expense accounts: debit - credit
            -- For revenue accounts: credit - debit
            SUM(CASE
                WHEN coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
                THEN jl.debit - jl.credit
                WHEN coa.account_type IN ('REVENUE', 'OTHER_INCOME')
                THEN jl.credit - jl.debit
                ELSE jl.debit - jl.credit
            END) as actual_amt
        FROM journal_lines jl
        JOIN journal_entries je ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON jl.account_id = coa.id
        WHERE je.tenant_id = current_setting('app.tenant_id', true)
        AND je.status = 'POSTED'
        AND je.journal_date BETWEEN v_start_date AND v_end_date
        GROUP BY jl.account_id, jl.cost_center_id
    )
    SELECT
        bd.account_id,
        bd.account_code,
        bd.account_name,
        bd.account_type,
        bd.cost_center_id,
        bd.cost_center_name,
        bd.budget_amt as budget_amount,
        COALESCE(ad.actual_amt, 0)::BIGINT as actual_amount,
        (bd.budget_amt - COALESCE(ad.actual_amt, 0))::BIGINT as variance,
        CASE
            WHEN bd.budget_amt = 0 THEN 0
            ELSE ROUND((COALESCE(ad.actual_amt, 0)::DECIMAL / bd.budget_amt) * 100, 2)
        END as percentage_used
    FROM budget_data bd
    LEFT JOIN actual_data ad ON bd.account_id = ad.account_id
        AND (bd.cost_center_id = ad.cost_center_id OR (bd.cost_center_id IS NULL AND ad.cost_center_id IS NULL))
    ORDER BY bd.account_code;
END;
$function$

;

-- ── get_cost_center_summary (versi V231, tanpa cast) ──
CREATE OR REPLACE FUNCTION public.get_cost_center_summary(p_cost_center_id uuid, p_start_date date, p_end_date date)
 RETURNS TABLE(account_type character varying, account_code character varying, account_name character varying, total_debit bigint, total_credit bigint, net_amount bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        coa.account_type,
        coa.account_code,
        coa.name as account_name,
        COALESCE(SUM(jl.debit), 0)::BIGINT as total_debit,
        COALESCE(SUM(jl.credit), 0)::BIGINT as total_credit,
        COALESCE(SUM(jl.debit - jl.credit), 0)::BIGINT as net_amount
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE jl.cost_center_id = p_cost_center_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_start_date AND p_end_date
    GROUP BY coa.account_type, coa.account_code, coa.name
    ORDER BY coa.account_code;
END;
$function$

;

DELETE FROM schema_migrations WHERE version = 'V232__cast_coa_text_to_varchar.sql';
