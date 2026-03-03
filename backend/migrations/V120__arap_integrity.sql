-- V120: AR/AP Integrity — Financial ratio fix + DB functions + triggers
-- Companion: milkyhoop-arap v1.0

-- ============================================================
-- PART 1: Fix V059 financial ratio function
-- ============================================================

CREATE OR REPLACE FUNCTION calculate_financial_ratios(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
) RETURNS JSON AS $$
DECLARE
    v_current_assets NUMERIC(18,2) := 0;
    v_total_assets NUMERIC(18,2) := 0;
    v_cash NUMERIC(18,2) := 0;
    v_inventory NUMERIC(18,2) := 0;
    v_receivables NUMERIC(18,2) := 0;
    v_current_liabilities NUMERIC(18,2) := 0;
    v_total_liabilities NUMERIC(18,2) := 0;
    v_payables NUMERIC(18,2) := 0;
    v_equity NUMERIC(18,2) := 0;
    v_revenue NUMERIC(18,2) := 0;
    v_cogs NUMERIC(18,2) := 0;
    v_expenses NUMERIC(18,2) := 0;
    v_net_income NUMERIC(18,2) := 0;
BEGIN
    SELECT COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END), 0) INTO v_current_assets
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('ASSET', 'RECEIVABLE')
    AND (coa.account_code LIKE '1-10%' OR coa.account_code LIKE '1-11%' OR coa.account_code LIKE '1-12%' OR coa.account_code LIKE '1-13%');

    SELECT COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END), 0) INTO v_cash
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_code IN ('1-10100', '1-10200', '1-10300');

    SELECT COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END), 0) INTO v_receivables
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'RECEIVABLE';

    SELECT COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.debit ELSE -jl.credit END), 0) INTO v_total_assets
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('ASSET', 'RECEIVABLE');

    SELECT COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END), 0) INTO v_current_liabilities
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('LIABILITY', 'PAYABLE') AND coa.account_code LIKE '2-10%';

    SELECT COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END), 0) INTO v_payables
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_type = 'PAYABLE';

    SELECT COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END), 0) INTO v_total_liabilities
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date
    AND coa.account_type IN ('LIABILITY', 'PAYABLE');

    SELECT COALESCE(SUM(CASE WHEN jl.credit > 0 THEN jl.credit ELSE -jl.debit END), 0) INTO v_equity
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date AND coa.account_type = 'EQUITY';

    SELECT COALESCE(SUM(jl.credit - jl.debit), 0) INTO v_revenue
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date AND coa.account_type = 'REVENUE';

    SELECT COALESCE(SUM(jl.debit - jl.credit), 0) INTO v_cogs
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date AND coa.account_type = 'COGS';

    SELECT COALESCE(SUM(jl.debit - jl.credit), 0) INTO v_expenses
    FROM journal_lines jl JOIN journal_entries je ON jl.journal_id = je.id JOIN chart_of_accounts coa ON jl.account_id = coa.id
    WHERE je.tenant_id = p_tenant_id AND je.status = 'POSTED' AND je.journal_date <= p_as_of_date AND coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE');

    v_net_income := v_revenue - v_cogs - v_expenses;

    RETURN json_build_object(
        'as_of_date', p_as_of_date,
        'balance_sheet', json_build_object(
            'current_assets', v_current_assets, 'total_assets', v_total_assets,
            'cash', v_cash, 'inventory', v_inventory, 'receivables', v_receivables,
            'current_liabilities', v_current_liabilities, 'total_liabilities', v_total_liabilities,
            'payables', v_payables, 'equity', v_equity),
        'income_statement', json_build_object(
            'revenue', v_revenue, 'cogs', v_cogs, 'expenses', v_expenses, 'net_income', v_net_income),
        'ratios', json_build_object(
            'current_ratio', CASE WHEN v_current_liabilities > 0 THEN ROUND(v_current_assets / v_current_liabilities, 4) ELSE NULL END,
            'quick_ratio', CASE WHEN v_current_liabilities > 0 THEN ROUND((v_current_assets - v_inventory) / v_current_liabilities, 4) ELSE NULL END,
            'cash_ratio', CASE WHEN v_current_liabilities > 0 THEN ROUND(v_cash / v_current_liabilities, 4) ELSE NULL END,
            'debt_to_equity', CASE WHEN v_equity > 0 THEN ROUND(v_total_liabilities / v_equity, 4) ELSE NULL END,
            'debt_ratio', CASE WHEN v_total_assets > 0 THEN ROUND(v_total_liabilities / v_total_assets, 4) ELSE NULL END,
            'gross_margin', CASE WHEN v_revenue > 0 THEN ROUND((v_revenue - v_cogs) / v_revenue, 4) ELSE NULL END,
            'net_margin', CASE WHEN v_revenue > 0 THEN ROUND(v_net_income / v_revenue, 4) ELSE NULL END,
            'return_on_assets', CASE WHEN v_total_assets > 0 THEN ROUND(v_net_income / v_total_assets, 4) ELSE NULL END,
            'return_on_equity', CASE WHEN v_equity > 0 THEN ROUND(v_net_income / v_equity, 4) ELSE NULL END,
            'receivables_turnover', CASE WHEN v_receivables > 0 THEN ROUND(v_revenue / v_receivables, 4) ELSE NULL END,
            'payables_turnover', CASE WHEN v_payables > 0 THEN ROUND(v_cogs / v_payables, 4) ELSE NULL END,
            'working_capital', v_current_assets - v_current_liabilities));
END;
$$ LANGUAGE plpgsql STABLE;


-- ============================================================
-- PART 2: compute_ap_outstanding()
-- bills table: invoice_number (not bill_number), issue_date (not bill_date), amount (not total_amount)
-- ============================================================

CREATE OR REPLACE FUNCTION compute_ap_outstanding(p_tenant_id TEXT)
RETURNS TABLE (
    vendor_id UUID,
    vendor_name TEXT,
    bill_id UUID,
    bill_number TEXT,
    bill_date DATE,
    due_date DATE,
    bill_status TEXT,
    bill_total NUMERIC(18,2),
    paid_amount NUMERIC(18,2),
    outstanding NUMERIC(18,2)
) LANGUAGE sql STABLE AS $$
    WITH
    active_bills AS (
        SELECT b.id, b.invoice_number, b.issue_date, b.due_date,
               b.vendor_id, b.vendor_name, b.status_v2, b.amount
        FROM bills b
        WHERE b.tenant_id = p_tenant_id
          AND b.status_v2 NOT IN ('draft', 'void')
    ),
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
    payment_debits AS (
        SELECT bill_id, COALESCE(SUM(total_debit), 0) AS total_debit
        FROM (
            SELECT bpa.bill_id, SUM(jl.debit) AS total_debit
            FROM bill_payment_allocations bpa
            JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
            JOIN journal_entries je ON je.id = bpv2.journal_id
            JOIN journal_lines jl ON jl.journal_id = je.id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE bpv2.tenant_id = p_tenant_id
              AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
              AND coa.account_type = 'PAYABLE' AND jl.debit > 0
            GROUP BY bpa.bill_id
            UNION ALL
            SELECT bp.bill_id, SUM(jl.debit) AS total_debit
            FROM bill_payments bp
            JOIN journal_entries je ON je.id = bp.journal_id
            JOIN journal_lines jl ON jl.journal_id = je.id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE bp.tenant_id = p_tenant_id
              AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
              AND coa.account_type = 'PAYABLE' AND jl.debit > 0
            GROUP BY bp.bill_id
        ) combined
        GROUP BY bill_id
    )
    SELECT ab.vendor_id, ab.vendor_name::TEXT, ab.id,
           ab.invoice_number::TEXT, ab.issue_date, ab.due_date,
           ab.status_v2::TEXT,
           COALESCE(bc.total_credit, 0)::NUMERIC(18,2),
           COALESCE(pd.total_debit, 0)::NUMERIC(18,2),
           (COALESCE(bc.total_credit, 0) - COALESCE(pd.total_debit, 0))::NUMERIC(18,2)
    FROM active_bills ab
    LEFT JOIN bill_credits bc ON bc.bill_id = ab.id
    LEFT JOIN payment_debits pd ON pd.bill_id = ab.id
    WHERE COALESCE(bc.total_credit, 0) - COALESCE(pd.total_debit, 0) != 0
    ORDER BY ab.vendor_name, ab.due_date;
$$;


-- ============================================================
-- PART 3: compute_ar_outstanding()
-- sales_invoices.customer_id is TEXT (not UUID!)
-- ============================================================

CREATE OR REPLACE FUNCTION compute_ar_outstanding(p_tenant_id TEXT)
RETURNS TABLE (
    customer_id TEXT,
    customer_name TEXT,
    invoice_id UUID,
    invoice_number TEXT,
    invoice_date DATE,
    due_date DATE,
    invoice_status TEXT,
    invoice_total NUMERIC(18,2),
    paid_amount NUMERIC(18,2),
    outstanding NUMERIC(18,2)
) LANGUAGE sql STABLE AS $$
    WITH
    active_invoices AS (
        SELECT si.id, si.invoice_number, si.invoice_date, si.due_date,
               si.customer_id, si.customer_name, si.status, si.total_amount
        FROM sales_invoices si
        WHERE si.tenant_id = p_tenant_id
          AND si.status NOT IN ('draft', 'void')
    ),
    invoice_debits AS (
        SELECT je.source_id::uuid AS invoice_id,
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
        SELECT invoice_id, COALESCE(SUM(total_credit), 0) AS total_credit
        FROM (
            SELECT rpa.invoice_id, SUM(jl.credit) AS total_credit
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
            SELECT je.source_id::uuid AS invoice_id, SUM(jl.credit) AS total_credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.tenant_id = p_tenant_id
              AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
              AND je.source_type = 'PAYMENT_RECEIVED'
              AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
              AND NOT EXISTS (
                  SELECT 1 FROM receive_payment_allocations rpa2
                  JOIN receive_payments rp2 ON rp2.id = rpa2.payment_id
                  WHERE rp2.journal_id = je.id
              )
            GROUP BY je.source_id::uuid
        ) combined
        GROUP BY invoice_id
    )
    SELECT ai.customer_id::TEXT, ai.customer_name::TEXT, ai.id,
           ai.invoice_number::TEXT, ai.invoice_date, ai.due_date,
           ai.status::TEXT,
           COALESCE(id2.total_debit, 0)::NUMERIC(18,2),
           COALESCE(pc.total_credit, 0)::NUMERIC(18,2),
           (COALESCE(id2.total_debit, 0) - COALESCE(pc.total_credit, 0))::NUMERIC(18,2)
    FROM active_invoices ai
    LEFT JOIN invoice_debits id2 ON id2.invoice_id = ai.id
    LEFT JOIN payment_credits pc ON pc.invoice_id = ai.id
    WHERE COALESCE(id2.total_debit, 0) - COALESCE(pc.total_credit, 0) != 0
    ORDER BY ai.customer_name, ai.due_date;
$$;


-- ============================================================
-- PART 4: Summary + per-entity wrappers
-- ============================================================

CREATE OR REPLACE FUNCTION compute_ap_summary(p_tenant_id TEXT)
RETURNS TABLE (total_outstanding NUMERIC(18,2), vendor_count BIGINT, overdue_amount NUMERIC(18,2), current_amount NUMERIC(18,2))
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(outstanding), 0)::NUMERIC(18,2), COUNT(DISTINCT vendor_id),
           COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE THEN outstanding ELSE 0 END), 0)::NUMERIC(18,2),
           COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0)::NUMERIC(18,2)
    FROM compute_ap_outstanding(p_tenant_id);
$$;

CREATE OR REPLACE FUNCTION compute_ar_summary(p_tenant_id TEXT)
RETURNS TABLE (total_outstanding NUMERIC(18,2), customer_count BIGINT, overdue_amount NUMERIC(18,2), current_amount NUMERIC(18,2))
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(outstanding), 0)::NUMERIC(18,2), COUNT(DISTINCT customer_id),
           COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE THEN outstanding ELSE 0 END), 0)::NUMERIC(18,2),
           COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0)::NUMERIC(18,2)
    FROM compute_ar_outstanding(p_tenant_id);
$$;

CREATE OR REPLACE FUNCTION compute_vendor_ap(p_tenant_id TEXT, p_vendor_id UUID)
RETURNS TABLE (bill_id UUID, bill_number TEXT, bill_date DATE, due_date DATE,
    bill_status TEXT, bill_total NUMERIC(18,2), paid_amount NUMERIC(18,2), outstanding NUMERIC(18,2))
LANGUAGE sql STABLE AS $$
    SELECT bill_id, bill_number, bill_date, due_date, bill_status, bill_total, paid_amount, outstanding
    FROM compute_ap_outstanding(p_tenant_id) WHERE vendor_id = p_vendor_id;
$$;

CREATE OR REPLACE FUNCTION compute_customer_ar(p_tenant_id TEXT, p_customer_id TEXT)
RETURNS TABLE (invoice_id UUID, invoice_number TEXT, invoice_date DATE, due_date DATE,
    invoice_status TEXT, invoice_total NUMERIC(18,2), paid_amount NUMERIC(18,2), outstanding NUMERIC(18,2))
LANGUAGE sql STABLE AS $$
    SELECT invoice_id, invoice_number, invoice_date, due_date, invoice_status, invoice_total, paid_amount, outstanding
    FROM compute_ar_outstanding(p_tenant_id) WHERE customer_id = p_customer_id;
$$;


-- ============================================================
-- PART 5: Trigger — auto-void settlement on journal reversal
-- ============================================================

CREATE OR REPLACE FUNCTION auto_void_settlement_on_reversal()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.reversed_by_id IS NULL AND NEW.reversed_by_id IS NOT NULL THEN
        UPDATE bill_payments_v2 SET status = 'voided', updated_at = NOW()
        WHERE journal_id = NEW.id AND status != 'voided';
        UPDATE receive_payments SET status = 'voided', updated_at = NOW()
        WHERE journal_id = NEW.id AND status != 'voided';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_auto_void_settlement_on_reversal' AND tgrelid = 'journal_entries'::regclass) THEN
        CREATE TRIGGER trg_auto_void_settlement_on_reversal
            AFTER UPDATE OF reversed_by_id ON journal_entries
            FOR EACH ROW WHEN (OLD.reversed_by_id IS NULL AND NEW.reversed_by_id IS NOT NULL)
            EXECUTE FUNCTION auto_void_settlement_on_reversal();
    END IF;
END $$;


-- ============================================================
-- PART 6: Trigger — guard phantom AR/AP
-- ============================================================

CREATE OR REPLACE FUNCTION guard_arap_requires_obligation()
RETURNS TRIGGER AS $$
DECLARE
    v_account_type TEXT;
    v_source_type TEXT;
    v_source_id TEXT;
    v_obligation_exists BOOLEAN;
    v_whitelisted TEXT[] := ARRAY[
        'BILL', 'PAYMENT_BILL', 'PAYMENT_MADE',
        'INVOICE', 'SALES_INVOICE_COGS', 'CASH_SALE',
        'PAYMENT_RECEIVED', 'RECEIVE_PAYMENT', 'INVOICE_REVERSAL',
        'OPENING', 'OPENING_BALANCE', 'MANUAL', 'ADJUSTMENT',
        'REVERSAL', 'CLOSING', 'REVALUATION',
        'CREDIT_NOTE', 'VENDOR_CREDIT', 'VENDOR_DEPOSIT',
        'CUSTOMER_DEPOSIT', 'BANK_TRANSACTION', 'BANK_TRANSFER',
        'RECONCILIATION_ADJUSTMENT', 'STOCK_ADJUSTMENT',
        'EXPENSE', 'SALES_RECEIPT', 'PAYROLL',
        'FIXED_ASSET', 'DEPRECIATION', 'ASSET_DISPOSAL', 'ASSET_SALE',
        'INTERCOMPANY', 'CHEQUE'
    ];
BEGIN
    SELECT coa.account_type INTO v_account_type FROM chart_of_accounts coa WHERE coa.id = NEW.account_id;
    IF v_account_type NOT IN ('RECEIVABLE', 'PAYABLE') THEN RETURN NEW; END IF;

    SELECT je.source_type, je.source_id INTO v_source_type, v_source_id
    FROM journal_entries je WHERE je.id = NEW.journal_id;

    IF v_source_type = ANY(v_whitelisted) THEN RETURN NEW; END IF;

    IF v_account_type = 'PAYABLE' AND NEW.credit > 0 THEN
        BEGIN
            SELECT EXISTS(SELECT 1 FROM bills WHERE id = v_source_id::uuid) INTO v_obligation_exists;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Law 29/30: Non-UUID source_id "%" cannot touch PAYABLE.', v_source_id;
        END;
        IF NOT v_obligation_exists THEN
            RAISE EXCEPTION 'Law 29/30: Cannot credit PAYABLE without bill. source_type=%, source_id=%', v_source_type, v_source_id;
        END IF;
    END IF;

    IF v_account_type = 'RECEIVABLE' AND NEW.debit > 0 THEN
        BEGIN
            SELECT EXISTS(SELECT 1 FROM sales_invoices WHERE id = v_source_id::uuid) INTO v_obligation_exists;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Law 29/30: Non-UUID source_id "%" cannot touch RECEIVABLE.', v_source_id;
        END;
        IF NOT v_obligation_exists THEN
            RAISE EXCEPTION 'Law 29/30: Cannot debit RECEIVABLE without invoice. source_type=%, source_id=%', v_source_type, v_source_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_guard_arap_requires_obligation' AND tgrelid = 'journal_lines'::regclass) THEN
        CREATE TRIGGER trg_guard_arap_requires_obligation
            BEFORE INSERT ON journal_lines FOR EACH ROW
            EXECUTE FUNCTION guard_arap_requires_obligation();
    END IF;
END $$;
