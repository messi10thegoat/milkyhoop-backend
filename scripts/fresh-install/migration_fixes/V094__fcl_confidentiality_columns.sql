-- V094 FIX: FCL Confidentiality Columns
-- Only ADD COLUMNs + indexes. RLS POLICY with ::UUID cast skipped (tenant_id is TEXT).

-- SECTION 1: ADD COLUMNS
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE bills ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE receive_payments ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';
ALTER TABLE bill_payments ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L3';

-- SECTION 2: ENABLE RLS (safe, no policy needed for RLS enable)
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

-- SECTION 4: CONDITIONAL TABLES (payroll_batches, employees)
DO $chk1$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'payroll_batches') THEN
        ALTER TABLE payroll_batches ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L4';
    END IF;
END
$chk1$;

DO $chk2$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'employees') THEN
        ALTER TABLE employees ADD COLUMN IF NOT EXISTS confidentiality_level confidentiality_level DEFAULT 'L4';
    END IF;
END
$chk2$;

-- SECTION 5: RLS POLICIES SKIPPED — tenant_id is TEXT (not UUID), ::UUID cast would fail.
-- Policies can be added later when tenant_id type is standardized.

-- SECTION 6: HELPER FUNCTION (simplified - no UUID cast)
CREATE OR REPLACE FUNCTION set_fcl_context(p_tenant_id TEXT, p_user_visibility TEXT DEFAULT 'L1,L2,L3')
RETURNS VOID AS $fn$
BEGIN
    PERFORM set_config('app.tenant_id', p_tenant_id, true);
    PERFORM set_config('app.user_visibility', p_user_visibility, true);
END;
$fn$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION set_fcl_context IS 'Set FCL context for current transaction';

-- SECTION 7: COLUMN COMMENTS
COMMENT ON COLUMN expenses.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN journal_entries.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN bills.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN sales_invoices.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN receive_payments.confidentiality_level IS 'FCL: L3 (Finance)';
COMMENT ON COLUMN bill_payments.confidentiality_level IS 'FCL: L3 (Finance)';
