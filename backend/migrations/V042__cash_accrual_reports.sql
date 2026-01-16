-- ============================================================================
-- V042: Cash/Accrual Report Switch
-- ============================================================================
-- Purpose: Enable switching between cash and accrual accounting basis for reports
-- Adds accounting settings per tenant and tracks payment dates for cash basis
-- ============================================================================

-- ============================================================================
-- 1. ACCOUNTING SETTINGS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS accounting_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL UNIQUE,

    -- Report basis preference
    default_report_basis VARCHAR(10) DEFAULT 'accrual', -- 'cash' or 'accrual'

    -- Fiscal year settings
    fiscal_year_start_month INTEGER DEFAULT 1, -- 1-12 (January default)

    -- Currency settings
    base_currency_code CHAR(3) DEFAULT 'IDR',

    -- Number formatting
    decimal_places INTEGER DEFAULT 0,
    thousand_separator VARCHAR(1) DEFAULT '.',
    decimal_separator VARCHAR(1) DEFAULT ',',

    -- Date format preference
    date_format VARCHAR(20) DEFAULT 'DD/MM/YYYY',

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_report_basis CHECK (default_report_basis IN ('cash', 'accrual')),
    CONSTRAINT chk_fiscal_month CHECK (fiscal_year_start_month BETWEEN 1 AND 12)
);

COMMENT ON TABLE accounting_settings IS 'Tenant-specific accounting preferences and report settings';
COMMENT ON COLUMN accounting_settings.default_report_basis IS 'Default basis for financial reports: cash or accrual';
COMMENT ON COLUMN accounting_settings.fiscal_year_start_month IS 'Month when fiscal year starts (1=Jan, 4=Apr for some regions)';

-- ============================================================================
-- 2. ADD FULLY_PAID_DATE TO SALES_INVOICES
-- ============================================================================

ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS fully_paid_date DATE;
CREATE INDEX IF NOT EXISTS idx_invoices_paid_date ON sales_invoices(tenant_id, fully_paid_date)
    WHERE fully_paid_date IS NOT NULL;

COMMENT ON COLUMN sales_invoices.fully_paid_date IS 'Date when invoice was fully paid - used for cash basis reporting';

-- ============================================================================
-- 3. ADD FULLY_PAID_DATE TO BILLS
-- ============================================================================

ALTER TABLE bills ADD COLUMN IF NOT EXISTS fully_paid_date DATE;
CREATE INDEX IF NOT EXISTS idx_bills_paid_date ON bills(tenant_id, fully_paid_date)
    WHERE fully_paid_date IS NOT NULL;

COMMENT ON COLUMN bills.fully_paid_date IS 'Date when bill was fully paid - used for cash basis reporting';

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE accounting_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_accounting_settings ON accounting_settings;
CREATE POLICY rls_accounting_settings ON accounting_settings
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 5. TRIGGERS TO AUTO-SET FULLY_PAID_DATE
-- ============================================================================

-- Trigger for sales_invoices
CREATE OR REPLACE FUNCTION set_invoice_fully_paid_date()
RETURNS TRIGGER AS $$
BEGIN
    -- Set fully_paid_date when status changes to 'paid'
    IF NEW.status = 'paid' AND (OLD.status IS DISTINCT FROM 'paid') THEN
        NEW.fully_paid_date := COALESCE(NEW.fully_paid_date, CURRENT_DATE);
    END IF;

    -- Clear fully_paid_date if status changes away from 'paid'
    IF OLD.status = 'paid' AND NEW.status != 'paid' THEN
        NEW.fully_paid_date := NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_invoice_fully_paid ON sales_invoices;
CREATE TRIGGER trg_invoice_fully_paid
BEFORE UPDATE ON sales_invoices
FOR EACH ROW EXECUTE FUNCTION set_invoice_fully_paid_date();

-- Trigger for bills
CREATE OR REPLACE FUNCTION set_bill_fully_paid_date()
RETURNS TRIGGER AS $$
BEGIN
    -- Set fully_paid_date when status changes to 'paid'
    IF NEW.status = 'paid' AND (OLD.status IS DISTINCT FROM 'paid') THEN
        NEW.fully_paid_date := COALESCE(NEW.fully_paid_date, CURRENT_DATE);
    END IF;

    -- Clear fully_paid_date if status changes away from 'paid'
    IF OLD.status = 'paid' AND NEW.status != 'paid' THEN
        NEW.fully_paid_date := NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bill_fully_paid ON bills;
CREATE TRIGGER trg_bill_fully_paid
BEFORE UPDATE ON bills
FOR EACH ROW EXECUTE FUNCTION set_bill_fully_paid_date();

-- ============================================================================
-- 6. UPDATE EXISTING PAID INVOICES/BILLS
-- ============================================================================

-- Backfill fully_paid_date for existing paid invoices
UPDATE sales_invoices
SET fully_paid_date = COALESCE(
    (SELECT MAX(payment_date) FROM payment_receipts pr
     WHERE pr.invoice_id = sales_invoices.id),
    updated_at::DATE,
    invoice_date
)
WHERE status = 'paid' AND fully_paid_date IS NULL;

-- Backfill fully_paid_date for existing paid bills
UPDATE bills
SET fully_paid_date = COALESCE(
    (SELECT MAX(payment_date) FROM payments_made pm
     WHERE pm.bill_id = bills.id),
    updated_at::DATE,
    bill_date
)
WHERE status = 'paid' AND fully_paid_date IS NULL;

-- ============================================================================
-- 7. INITIALIZE SETTINGS FOR EXISTING TENANTS
-- ============================================================================

INSERT INTO accounting_settings (tenant_id, default_report_basis)
SELECT DISTINCT tenant_id, 'accrual'
FROM chart_of_accounts
WHERE tenant_id NOT IN (SELECT tenant_id FROM accounting_settings)
ON CONFLICT (tenant_id) DO NOTHING;

-- ============================================================================
-- 8. HELPER FUNCTIONS FOR CASH/ACCRUAL REPORTING
-- ============================================================================

-- Get revenue for a period based on accounting basis
CREATE OR REPLACE FUNCTION get_revenue_by_basis(
    p_tenant_id TEXT,
    p_start_date DATE,
    p_end_date DATE,
    p_basis VARCHAR(10) DEFAULT 'accrual'
) RETURNS TABLE (
    account_id UUID,
    account_code VARCHAR(20),
    account_name VARCHAR(255),
    total_amount BIGINT
) AS $$
BEGIN
    IF p_basis = 'cash' THEN
        -- Cash basis: Revenue when payment received
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.account_name,
            SUM(jl.credit - jl.debit)::BIGINT as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_entry_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.entry_date BETWEEN p_start_date AND p_end_date
        AND je.source_type IN ('PAYMENT_RECEIPT', 'CASH_SALE')
        AND coa.account_type = 'REVENUE'
        GROUP BY jl.account_id, coa.account_code, coa.account_name
        HAVING SUM(jl.credit - jl.debit) != 0;
    ELSE
        -- Accrual basis: Revenue when invoice issued
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.account_name,
            SUM(jl.credit - jl.debit)::BIGINT as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_entry_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.entry_date BETWEEN p_start_date AND p_end_date
        AND coa.account_type = 'REVENUE'
        GROUP BY jl.account_id, coa.account_code, coa.account_name
        HAVING SUM(jl.credit - jl.debit) != 0;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Get expenses for a period based on accounting basis
CREATE OR REPLACE FUNCTION get_expenses_by_basis(
    p_tenant_id TEXT,
    p_start_date DATE,
    p_end_date DATE,
    p_basis VARCHAR(10) DEFAULT 'accrual'
) RETURNS TABLE (
    account_id UUID,
    account_code VARCHAR(20),
    account_name VARCHAR(255),
    total_amount BIGINT
) AS $$
BEGIN
    IF p_basis = 'cash' THEN
        -- Cash basis: Expense when payment made
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.account_name,
            SUM(jl.debit - jl.credit)::BIGINT as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_entry_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.entry_date BETWEEN p_start_date AND p_end_date
        AND je.source_type IN ('PAYMENT_MADE', 'EXPENSE', 'CASH_PURCHASE')
        AND coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
        GROUP BY jl.account_id, coa.account_code, coa.account_name
        HAVING SUM(jl.debit - jl.credit) != 0;
    ELSE
        -- Accrual basis: Expense when bill/expense recorded
        RETURN QUERY
        SELECT
            jl.account_id,
            coa.account_code,
            coa.account_name,
            SUM(jl.debit - jl.credit)::BIGINT as total_amount
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_entry_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
        AND je.entry_date BETWEEN p_start_date AND p_end_date
        AND coa.account_type IN ('EXPENSE', 'COGS', 'OTHER_EXPENSE')
        GROUP BY jl.account_id, coa.account_code, coa.account_name
        HAVING SUM(jl.debit - jl.credit) != 0;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_accounting_settings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_accounting_settings_updated_at ON accounting_settings;
CREATE TRIGGER trg_accounting_settings_updated_at
BEFORE UPDATE ON accounting_settings
FOR EACH ROW EXECUTE FUNCTION update_accounting_settings_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V042: Cash/Accrual Report Switch created successfully';
    RAISE NOTICE 'Tables: accounting_settings';
    RAISE NOTICE 'Columns added: sales_invoices.fully_paid_date, bills.fully_paid_date';
    RAISE NOTICE 'Triggers: auto-set fully_paid_date on status change to paid';
    RAISE NOTICE 'Functions: get_revenue_by_basis(), get_expenses_by_basis()';
    RAISE NOTICE 'Default basis: accrual';
END $$;
