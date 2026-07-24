-- ============================================================================
-- V199__financial_ratios_include_receivable_payable.sql
--
-- BUG PELAPORAN + penutupan instance kedua dari kelas V197.
--
-- KELAS: V120 berjudul "PART 1: Fix V059 financial ratio function" — jelas
-- dimaksudkan MENGGANTI calculate_financial_ratios milik V059. Tapi V120
-- menulis signature (TEXT, DATE DEFAULT CURRENT_DATE) sedangkan V059
-- (TEXT, DATE, DATE DEFAULT NULL, DATE DEFAULT NULL). Beda arity ->
-- OVERLOAD BARU, bukan pengganti. Sama persis dengan bug V197.
--
-- AKIBATNYA:
--   a) Seluruh call-site memanggil dengan 4 argumen (financial_ratios.py:148,
--      270, 516, 524, 738, 947) sehingga SELALU me-resolve ke versi V059 —
--      perbaikan V120 TIDAK PERNAH JALAN sejak V120 di-deploy.
--   b) Panggilan 2-argumen apa pun hard-fail:
--      "function calculate_financial_ratios(text, date) is not unique".
--
-- CACAT YANG DIPERBAIKI V120 (nyata, terkonfirmasi di DB):
--   V059 hanya mengenali account_type 'ASSET' dan 'LIABILITY'. Padahal CoA
--   default memakai tipe TERPISAH untuk AR/AP:
--       1-10400 Piutang Usaha -> RECEIVABLE
--       2-10100 Hutang Usaha  -> PAYABLE
--   Sehingga Piutang Usaha TIDAK IKUT current assets dan Hutang Usaha TIDAK
--   IKUT current liabilities -> current_ratio, quick_ratio, cash_ratio,
--   debt_ratio, dan debt_to_equity SALAH SAJI untuk semua tenant.
--
-- FIX: port perbaikan itu ke signature 4-argumen (yang benar-benar dipakai),
-- lalu DROP overload 2-argumen yang mati.
--
-- METODE: fungsi di bawah diturunkan dari definisi yang HIDUP DI DATABASE
-- (pg_get_functiondef), bukan disalin dari file V059 — konsisten dengan V198.
-- Hanya predikat ASSET dan LIABILITY yang diperluas; EQUITY/REVENUE/EXPENSE
-- tidak disentuh.
--
-- CATATAN (sengaja TIDAK di-port): V120 juga menghitung gross_margin dan
-- net_margin yang tak ada di V059, dan RETURNS JSON (bukan JSONB). Menambah
-- metrik mengubah kontrak keluaran yang dikonsumsi frontend, jadi di luar
-- lingkup perbaikan korektif ini. Dicatat sebagai follow-up terpisah.
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.calculate_financial_ratios(p_tenant_id text, p_as_of_date date, p_period_start date DEFAULT NULL::date, p_period_end date DEFAULT NULL::date)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    v_result JSONB;
    v_current_assets BIGINT := 0;
    v_current_liabilities BIGINT := 0;
    v_total_assets BIGINT := 0;
    v_total_liabilities BIGINT := 0;
    v_equity BIGINT := 0;
    v_inventory BIGINT := 0;
    v_cash BIGINT := 0;
    v_receivables BIGINT := 0;
    v_payables BIGINT := 0;
    v_revenue BIGINT := 0;
    v_cogs BIGINT := 0;
    v_operating_income BIGINT := 0;
    v_net_income BIGINT := 0;
    v_interest_expense BIGINT := 0;
    v_ratios JSONB := '{}'::JSONB;
    v_source_data JSONB;
BEGIN
    -- Set default period if not provided
    IF p_period_start IS NULL THEN
        p_period_start := date_trunc('year', p_as_of_date)::DATE;
    END IF;
    IF p_period_end IS NULL THEN
        p_period_end := p_as_of_date;
    END IF;

    -- Get balance sheet figures from chart_of_accounts balances
    -- Current Assets (account codes starting with 1-10, 1-11, 1-12, 1-13)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_current_assets
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('ASSET', 'RECEIVABLE')
    AND (coa.account_code LIKE '1-10%' OR coa.account_code LIKE '1-11%'
         OR coa.account_code LIKE '1-12%' OR coa.account_code LIKE '1-13%');

    -- Cash specifically (1-10100, 1-10200)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_cash
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code IN ('1-10100', '1-10200');

    -- Inventory (1-10400)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_inventory
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code LIKE '1-104%';

    -- Receivables (1-10300)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_receivables
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code LIKE '1-103%';

    -- Total Assets
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_total_assets
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('ASSET', 'RECEIVABLE');

    -- Current Liabilities (2-10%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_current_liabilities
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('LIABILITY', 'PAYABLE')
    AND coa.account_code LIKE '2-10%';

    -- Payables (2-10100)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_payables
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_code = '2-10100';

    -- Total Liabilities
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_total_liabilities
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('LIABILITY', 'PAYABLE');

    -- Equity (3-%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_equity
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'EQUITY';

    -- Revenue for period (4-%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END
    ), 0) INTO v_revenue
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_period_start AND p_period_end
    AND coa.account_type = 'REVENUE';

    -- COGS for period (5-10%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_cogs
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_period_start AND p_period_end
    AND coa.account_code LIKE '5-10%';

    -- Operating expenses for period (5-20%, 5-30%)
    SELECT COALESCE(SUM(
        CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END
    ), 0) INTO v_operating_income
    FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id
    AND je.status = 'POSTED'
    AND je.journal_date BETWEEN p_period_start AND p_period_end
    AND coa.account_type = 'EXPENSE';

    -- Calculate net income
    v_net_income := v_revenue - v_cogs - v_operating_income;
    v_operating_income := v_revenue - v_cogs - v_operating_income;

    -- Store source data
    v_source_data := jsonb_build_object(
        'current_assets', v_current_assets,
        'current_liabilities', v_current_liabilities,
        'total_assets', v_total_assets,
        'total_liabilities', v_total_liabilities,
        'equity', v_equity,
        'inventory', v_inventory,
        'cash', v_cash,
        'receivables', v_receivables,
        'payables', v_payables,
        'revenue', v_revenue,
        'cogs', v_cogs,
        'operating_income', v_operating_income,
        'net_income', v_net_income
    );

    -- Calculate ratios (avoiding division by zero)
    v_ratios := jsonb_build_object(
        'liquidity', jsonb_build_object(
            'current_ratio', CASE WHEN v_current_liabilities > 0
                THEN ROUND((v_current_assets::NUMERIC / v_current_liabilities), 4) ELSE NULL END,
            'quick_ratio', CASE WHEN v_current_liabilities > 0
                THEN ROUND(((v_current_assets - v_inventory)::NUMERIC / v_current_liabilities), 4) ELSE NULL END,
            'cash_ratio', CASE WHEN v_current_liabilities > 0
                THEN ROUND((v_cash::NUMERIC / v_current_liabilities), 4) ELSE NULL END,
            'working_capital', v_current_assets - v_current_liabilities
        ),
        'profitability', jsonb_build_object(
            'gross_profit_margin', CASE WHEN v_revenue > 0
                THEN ROUND(((v_revenue - v_cogs)::NUMERIC / v_revenue * 100), 2) ELSE NULL END,
            'net_profit_margin', CASE WHEN v_revenue > 0
                THEN ROUND((v_net_income::NUMERIC / v_revenue * 100), 2) ELSE NULL END,
            'roe', CASE WHEN v_equity > 0
                THEN ROUND((v_net_income::NUMERIC / v_equity * 100), 2) ELSE NULL END,
            'roa', CASE WHEN v_total_assets > 0
                THEN ROUND((v_net_income::NUMERIC / v_total_assets * 100), 2) ELSE NULL END
        ),
        'efficiency', jsonb_build_object(
            'asset_turnover', CASE WHEN v_total_assets > 0
                THEN ROUND((v_revenue::NUMERIC / v_total_assets), 4) ELSE NULL END,
            'inventory_turnover', CASE WHEN v_inventory > 0
                THEN ROUND((v_cogs::NUMERIC / v_inventory), 4) ELSE NULL END,
            'days_inventory', CASE WHEN v_cogs > 0 AND v_inventory > 0
                THEN ROUND((365.0 * v_inventory / v_cogs), 0) ELSE NULL END,
            'receivables_turnover', CASE WHEN v_receivables > 0
                THEN ROUND((v_revenue::NUMERIC / v_receivables), 4) ELSE NULL END,
            'days_receivable', CASE WHEN v_revenue > 0 AND v_receivables > 0
                THEN ROUND((365.0 * v_receivables / v_revenue), 0) ELSE NULL END,
            'payables_turnover', CASE WHEN v_payables > 0
                THEN ROUND((v_cogs::NUMERIC / v_payables), 4) ELSE NULL END,
            'days_payable', CASE WHEN v_cogs > 0 AND v_payables > 0
                THEN ROUND((365.0 * v_payables / v_cogs), 0) ELSE NULL END
        ),
        'leverage', jsonb_build_object(
            'debt_ratio', CASE WHEN v_total_assets > 0
                THEN ROUND((v_total_liabilities::NUMERIC / v_total_assets * 100), 2) ELSE NULL END,
            'debt_to_equity', CASE WHEN v_equity > 0
                THEN ROUND((v_total_liabilities::NUMERIC / v_equity), 4) ELSE NULL END,
            'equity_ratio', CASE WHEN v_total_assets > 0
                THEN ROUND((v_equity::NUMERIC / v_total_assets * 100), 2) ELSE NULL END
        )
    );

    -- Build result
    v_result := jsonb_build_object(
        'calculated_at', NOW(),
        'as_of_date', p_as_of_date,
        'period_start', p_period_start,
        'period_end', p_period_end,
        'ratios', v_ratios,
        'source_data', v_source_data
    );

    RETURN v_result;
END;
$function$;

DROP FUNCTION IF EXISTS public.calculate_financial_ratios(text, date);

DO $v199$
DECLARE
    v_n INT;
    v_def TEXT;
BEGIN
    SELECT COUNT(*) INTO v_n FROM pg_proc WHERE proname = 'calculate_financial_ratios';
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'V199: calculate_financial_ratios harus tersisa TEPAT 1 overload, ditemukan %', v_n;
    END IF;
    SELECT pg_get_functiondef(oid) INTO v_def FROM pg_proc WHERE proname = 'calculate_financial_ratios';
    IF v_def NOT LIKE '%RECEIVABLE%' OR v_def NOT LIKE '%PAYABLE%' THEN
        RAISE EXCEPTION 'V199: fungsi final tidak menyertakan RECEIVABLE/PAYABLE';
    END IF;
END $v199$;

COMMIT;
