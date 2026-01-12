-- V020: AP Reconciliation Functions
-- Adds audit function and reconciliation check for Bills <-> AP integrity
-- Golden Rule: GL_AP_Balance == SUM(bills WHERE status NOT IN ('paid', 'void'))

-- ============================================================================
-- 1. AUDIT FUNCTION: Find divergent records
-- ============================================================================
CREATE OR REPLACE FUNCTION audit_ap_divergence(p_tenant_id TEXT)
RETURNS TABLE (
    issue_type TEXT,
    record_id UUID,
    record_number TEXT,
    amount BIGINT,
    expected BIGINT,
    actual BIGINT
) AS $$
BEGIN
    -- Bills without AP record
    RETURN QUERY
    SELECT 'BILL_NO_AP'::TEXT, b.id, b.invoice_number, b.amount, b.amount, 0::BIGINT
    FROM bills b
    WHERE b.tenant_id = p_tenant_id
      AND b.ap_id IS NULL
      AND b.status NOT IN ('void', 'paid');

    -- Bills without Journal Entry
    RETURN QUERY
    SELECT 'BILL_NO_JOURNAL'::TEXT, b.id, b.invoice_number, b.amount, b.amount, 0::BIGINT
    FROM bills b
    WHERE b.tenant_id = p_tenant_id
      AND b.journal_id IS NULL
      AND b.status NOT IN ('void', 'paid');

    -- AP without matching Bill
    RETURN QUERY
    SELECT 'AP_NO_BILL'::TEXT, ap.id, ap.bill_number, ap.amount::BIGINT, 0::BIGINT, ap.amount::BIGINT
    FROM accounts_payable ap
    LEFT JOIN bills b ON b.ap_id = ap.id
    WHERE ap.tenant_id = p_tenant_id
      AND b.id IS NULL
      AND ap.status NOT IN ('VOID', 'PAID');

    -- Amount mismatch between Bill and AP
    RETURN QUERY
    SELECT 'AMOUNT_MISMATCH'::TEXT, b.id, b.invoice_number, b.amount, b.amount, ap.amount::BIGINT
    FROM bills b
    JOIN accounts_payable ap ON ap.id = b.ap_id
    WHERE b.tenant_id = p_tenant_id
      AND b.amount != ap.amount::BIGINT
      AND b.status NOT IN ('void', 'paid');

    -- Amount_paid mismatch between Bill and AP
    RETURN QUERY
    SELECT 'PAID_MISMATCH'::TEXT, b.id, b.invoice_number, b.amount_paid, b.amount_paid, ap.amount_paid::BIGINT
    FROM bills b
    JOIN accounts_payable ap ON ap.id = b.ap_id
    WHERE b.tenant_id = p_tenant_id
      AND b.amount_paid != ap.amount_paid::BIGINT
      AND b.status NOT IN ('void');
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION audit_ap_divergence IS 'Finds data inconsistencies between bills and accounts_payable tables';

-- ============================================================================
-- 2. RECONCILIATION CHECK FUNCTION: Compare totals
-- ============================================================================
CREATE OR REPLACE FUNCTION check_ap_reconciliation(p_tenant_id TEXT)
RETURNS TABLE (
    bills_outstanding BIGINT,
    ap_subledger DECIMAL(18,2),
    gl_ap_balance DECIMAL(18,2),
    variance_bills_ap DECIMAL(18,2),
    variance_ap_gl DECIMAL(18,2),
    is_in_sync BOOLEAN
) AS $$
DECLARE
    v_bills_total BIGINT;
    v_ap_total DECIMAL(18,2);
    v_gl_balance DECIMAL(18,2);
    v_var_bills_ap DECIMAL(18,2);
    v_var_ap_gl DECIMAL(18,2);
BEGIN
    -- Outstanding Bills total
    SELECT COALESCE(SUM(amount - amount_paid), 0) INTO v_bills_total
    FROM bills
    WHERE tenant_id = p_tenant_id
      AND status NOT IN ('paid', 'void');

    -- AP Subledger total
    SELECT COALESCE(SUM(amount - amount_paid), 0) INTO v_ap_total
    FROM accounts_payable
    WHERE tenant_id = p_tenant_id
      AND status IN ('OPEN', 'PARTIAL');

    -- GL AP Account balance (account 2-10100)
    -- Formula: SUM(credit - debit) for liability account
    SELECT COALESCE(SUM(jl.credit - jl.debit), 0) INTO v_gl_balance
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id AND je.journal_date = jl.journal_date
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = p_tenant_id
      AND je.status = 'POSTED'
      AND coa.code = '2-10100';

    -- Calculate variances
    v_var_bills_ap := v_bills_total::DECIMAL - v_ap_total;
    v_var_ap_gl := v_ap_total - v_gl_balance;

    RETURN QUERY SELECT
        v_bills_total,
        v_ap_total,
        v_gl_balance,
        v_var_bills_ap,
        v_var_ap_gl,
        (ABS(v_var_bills_ap) < 0.01 AND ABS(v_var_ap_gl) < 0.01);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION check_ap_reconciliation IS 'Compares GL AP balance vs Outstanding Bills - should always be in sync';

-- ============================================================================
-- 3. FIX: Create missing AP records for existing bills
-- ============================================================================
DO $$
DECLARE
    v_count INT;
BEGIN
    -- Check if there are bills without AP
    SELECT COUNT(*) INTO v_count
    FROM bills b
    WHERE b.ap_id IS NULL
      AND b.status NOT IN ('void', 'paid');

    IF v_count > 0 THEN
        RAISE NOTICE 'Found % bills without AP records. Creating missing AP records...', v_count;

        INSERT INTO accounts_payable (
            id, tenant_id, supplier_id, supplier_name, bill_number, bill_date,
            due_date, amount, amount_paid, status, description, source_type, source_id,
            created_at
        )
        SELECT
            gen_random_uuid(),
            b.tenant_id,
            b.vendor_id,
            b.vendor_name,
            b.invoice_number,
            b.issue_date,
            b.due_date,
            b.amount::DECIMAL(18,2),
            b.amount_paid::DECIMAL(18,2),
            CASE b.status
                WHEN 'unpaid' THEN 'OPEN'
                WHEN 'partial' THEN 'PARTIAL'
                WHEN 'overdue' THEN 'OPEN'
                WHEN 'paid' THEN 'PAID'
                WHEN 'void' THEN 'VOID'
            END,
            b.notes,
            'BILL',
            b.id,
            b.created_at
        FROM bills b
        WHERE b.ap_id IS NULL
          AND b.status NOT IN ('void', 'paid')
        ON CONFLICT DO NOTHING;

        -- Update bills to link to newly created AP
        UPDATE bills b
        SET ap_id = ap.id
        FROM accounts_payable ap
        WHERE ap.source_id = b.id
          AND ap.source_type = 'BILL'
          AND b.ap_id IS NULL;

        RAISE NOTICE 'AP records created and linked.';
    ELSE
        RAISE NOTICE 'No bills without AP records found.';
    END IF;
END $$;

-- ============================================================================
-- 4. RECONCILIATION TRIGGER (WARNING mode - Phase 1)
-- Switch to EXCEPTION after data is clean
-- ============================================================================
CREATE OR REPLACE FUNCTION trg_check_ap_reconciliation()
RETURNS TRIGGER AS $$
DECLARE
    v_bills_total BIGINT;
    v_ap_total DECIMAL(18,2);
    v_variance DECIMAL(18,2);
BEGIN
    -- Only check on INSERT/UPDATE to bills or accounts_payable
    SELECT COALESCE(SUM(amount - amount_paid), 0) INTO v_bills_total
    FROM bills WHERE tenant_id = NEW.tenant_id AND status NOT IN ('paid', 'void');

    SELECT COALESCE(SUM(amount - amount_paid), 0) INTO v_ap_total
    FROM accounts_payable WHERE tenant_id = NEW.tenant_id AND status IN ('OPEN', 'PARTIAL');

    v_variance := ABS(v_bills_total::DECIMAL - v_ap_total);

    IF v_variance > 0.01 THEN
        -- Phase 1: WARNING (migration period) - allows transaction to proceed
        RAISE WARNING '[AP_RECONCILIATION] Variance detected for tenant %: Bills=%, AP=%, Variance=%',
            NEW.tenant_id, v_bills_total, v_ap_total, v_variance;

        -- Phase 2: Uncomment to enforce strict mode (blocks transaction)
        -- RAISE EXCEPTION '[AP_RECONCILIATION] Transaction blocked - variance: Bills=%, AP=%, Variance=%',
        --     v_bills_total, v_ap_total, v_variance;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Note: Trigger not attached by default to avoid performance impact
-- Uncomment below to enable on bills table:
-- CREATE TRIGGER trg_bills_ap_reconciliation
--     AFTER INSERT OR UPDATE ON bills
--     FOR EACH ROW
--     EXECUTE FUNCTION trg_check_ap_reconciliation();

COMMENT ON FUNCTION trg_check_ap_reconciliation IS 'Trigger function to validate AP reconciliation - WARNING mode by default';

-- ============================================================================
-- 5. REPORTING VIEW: AP Reconciliation Status per Tenant
-- ============================================================================
CREATE OR REPLACE VIEW v_ap_reconciliation_status AS
SELECT
    t.id as tenant_id,
    t.nama as tenant_name,
    r.*
FROM tenant t
CROSS JOIN LATERAL check_ap_reconciliation(t.id) r;

COMMENT ON VIEW v_ap_reconciliation_status IS 'Shows AP reconciliation status for all tenants';

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON FUNCTION audit_ap_divergence IS '
Finds data inconsistencies between bills and accounts_payable tables.

Returns records with issues:
- BILL_NO_AP: Bill exists but no AP record
- BILL_NO_JOURNAL: Bill exists but no journal entry
- AP_NO_BILL: AP record exists but no matching bill
- AMOUNT_MISMATCH: Bill amount != AP amount
- PAID_MISMATCH: Bill amount_paid != AP amount_paid

Usage: SELECT * FROM audit_ap_divergence(''tenant-001'');
';

COMMENT ON FUNCTION check_ap_reconciliation IS '
Compares GL AP balance vs Outstanding Bills.

Golden Rule: GL_AP_Balance == SUM(bills WHERE status NOT IN (paid, void))

Returns:
- bills_outstanding: Total unpaid bills
- ap_subledger: Total AP balance
- gl_ap_balance: GL balance for account 2-10100
- variance_bills_ap: Difference between bills and AP
- variance_ap_gl: Difference between AP and GL
- is_in_sync: TRUE if all variances are within tolerance (0.01)

Usage: SELECT * FROM check_ap_reconciliation(''tenant-001'');
';
