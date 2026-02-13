-- ============================================================
-- V094: FCL Confidentiality Columns for Transaction Tables
-- ============================================================
-- Prerequisites: V093 must define ENUM confidentiality_level
--
-- Iron Laws Compliance:
-- - Law 1: Ledger Supremacy - FCL filtering consistent across all queries
-- - Law 11: Event-Sourced State - FCL level recorded in journal
-- ============================================================

-- SECTION 1: ADD COLUMNS
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE bills ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE receive_payments ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE bill_payments ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';

-- SECTION 2: ENABLE RLS
ALTER TABLE expenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE bills ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE receive_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_payments ENABLE ROW LEVEL SECURITY;

-- SECTION 3: INDEXES
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_confidentiality ON expenses(tenant_id, confidentiality_level);
CREATE INDEX IF NOT EXISTS idx_journal_entries_tenant_confidentiality ON journal_entries(tenant_id, confidentiality_level);
CREATE INDEX IF NOT EXISTS idx_bills_tenant_confidentiality ON bills(tenant_id, confidentiality_level);
CREATE INDEX IF NOT EXISTS idx_sales_invoices_tenant_confidentiality ON sales_invoices(tenant_id, confidentiality_level);
CREATE INDEX IF NOT EXISTS idx_receive_payments_tenant_confidentiality ON receive_payments(tenant_id, confidentiality_level);
CREATE INDEX IF NOT EXISTS idx_bill_payments_tenant_confidentiality ON bill_payments(tenant_id, confidentiality_level);


-- ============================================================
-- SECTION 4: CONDITIONAL TABLES (payroll_batches, employees)
-- ============================================================

-- payroll_batches - Default L4 (Payroll) [CONDITIONAL]
DO $chk1$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'payroll_batches') THEN
        EXECUTE 'ALTER TABLE payroll_batches ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT '"'"'L4'"'"'';
    END IF;
END
$chk1$;

-- employees - Default L4 (Payroll) [CONDITIONAL]
DO $chk2$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'employees') THEN
        EXECUTE 'ALTER TABLE employees ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT '"'"'L4'"'"'';
    END IF;
END
$chk2$;


-- ============================================================
-- SECTION 5: RLS POLICIES FOR FCL FILTERING
-- ============================================================

DROP POLICY IF EXISTS fcl_expenses ON expenses;
DROP POLICY IF EXISTS fcl_journal_entries ON journal_entries;
DROP POLICY IF EXISTS fcl_bills ON bills;
DROP POLICY IF EXISTS fcl_sales_invoices ON sales_invoices;
DROP POLICY IF EXISTS fcl_receive_payments ON receive_payments;
DROP POLICY IF EXISTS fcl_bill_payments ON bill_payments;

CREATE POLICY fcl_expenses ON expenses FOR ALL USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::UUID
    AND (current_setting('app.user_visibility', true) IS NULL
         OR current_setting('app.user_visibility', true) = ''
         OR confidentiality_level = ANY(string_to_array(current_setting('app.user_visibility', true), ',')::confidentiality_level[]))
);

CREATE POLICY fcl_journal_entries ON journal_entries FOR ALL USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::UUID
    AND (current_setting('app.user_visibility', true) IS NULL
         OR current_setting('app.user_visibility', true) = ''
         OR confidentiality_level = ANY(string_to_array(current_setting('app.user_visibility', true), ',')::confidentiality_level[]))
);

CREATE POLICY fcl_bills ON bills FOR ALL USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::UUID
    AND (current_setting('app.user_visibility', true) IS NULL
         OR current_setting('app.user_visibility', true) = ''
         OR confidentiality_level = ANY(string_to_array(current_setting('app.user_visibility', true), ',')::confidentiality_level[]))
);

CREATE POLICY fcl_sales_invoices ON sales_invoices FOR ALL USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::UUID
    AND (current_setting('app.user_visibility', true) IS NULL
         OR current_setting('app.user_visibility', true) = ''
         OR confidentiality_level = ANY(string_to_array(current_setting('app.user_visibility', true), ',')::confidentiality_level[]))
);

CREATE POLICY fcl_receive_payments ON receive_payments FOR ALL USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::UUID
    AND (current_setting('app.user_visibility', true) IS NULL
         OR current_setting('app.user_visibility', true) = ''
         OR confidentiality_level = ANY(string_to_array(current_setting('app.user_visibility', true), ',')::confidentiality_level[]))
);

CREATE POLICY fcl_bill_payments ON bill_payments FOR ALL USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::UUID
    AND (current_setting('app.user_visibility', true) IS NULL
         OR current_setting('app.user_visibility', true) = ''
         OR confidentiality_level = ANY(string_to_array(current_setting('app.user_visibility', true), ',')::confidentiality_level[]))
);


-- ============================================================
-- SECTION 6: HELPER FUNCTION
-- ============================================================

CREATE OR REPLACE FUNCTION set_fcl_context(p_tenant_id UUID, p_user_visibility TEXT DEFAULT 'L1,L2,L3')
RETURNS VOID AS $fn$
BEGIN
    PERFORM set_config('app.tenant_id', p_tenant_id::TEXT, true);
    PERFORM set_config('app.user_visibility', p_user_visibility, true);
END;
$fn$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION set_fcl_context IS 'Set FCL context for current transaction';


-- ============================================================
-- SECTION 7: COLUMN COMMENTS
-- ============================================================

COMMENT ON COLUMN expenses.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN journal_entries.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN bills.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN sales_invoices.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN receive_payments.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN bill_payments.confidentiality_level IS 'FCL: L3 (Finance)';
