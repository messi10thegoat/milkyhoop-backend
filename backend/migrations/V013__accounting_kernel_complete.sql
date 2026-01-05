-- =============================================================================
-- Migration V013: Accounting Kernel Complete Schema (TEXT tenant_id)
-- =============================================================================
-- Complete accounting kernel schema using TEXT tenant_id to match existing system
-- =============================================================================

-- ============================================
-- 1. Chart of Accounts
-- ============================================
CREATE TABLE chart_of_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    account_code TEXT NOT NULL,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE')),
    normal_balance TEXT NOT NULL CHECK (normal_balance IN ('DEBIT', 'CREDIT')),
    parent_code TEXT,
    level INTEGER NOT NULL DEFAULT 1,
    is_header BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN NOT NULL DEFAULT true,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_coa_tenant_code UNIQUE (tenant_id, account_code)
);

CREATE INDEX idx_coa_tenant ON chart_of_accounts(tenant_id);
CREATE INDEX idx_coa_type ON chart_of_accounts(tenant_id, account_type);
CREATE INDEX idx_coa_parent ON chart_of_accounts(tenant_id, parent_code);
CREATE INDEX idx_coa_active ON chart_of_accounts(tenant_id, is_active);

COMMENT ON TABLE chart_of_accounts IS 'Chart of Accounts (Bagan Akun)';

-- ============================================
-- 2. Fiscal Periods
-- ============================================
CREATE TABLE fiscal_periods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    period_name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'LOCKED')),
    closed_at TIMESTAMPTZ,
    closed_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fiscal_tenant_dates UNIQUE (tenant_id, start_date, end_date)
);

CREATE INDEX idx_fiscal_tenant ON fiscal_periods(tenant_id);
CREATE INDEX idx_fiscal_dates ON fiscal_periods(tenant_id, start_date, end_date);

COMMENT ON TABLE fiscal_periods IS 'Fiscal periods for period closing';

-- ============================================
-- 3. Journal Entries (Non-Partitioned for simplicity)
-- ============================================
CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    journal_number TEXT NOT NULL,
    journal_date DATE NOT NULL,
    description TEXT NOT NULL,
    source_type TEXT,
    source_id UUID,
    trace_id TEXT,
    status TEXT NOT NULL DEFAULT 'POSTED' CHECK (status IN ('DRAFT', 'POSTED', 'VOID')),
    total_debit DECIMAL(18,2) NOT NULL DEFAULT 0,
    total_credit DECIMAL(18,2) NOT NULL DEFAULT 0,
    created_by UUID,
    voided_by UUID,
    voided_at TIMESTAMPTZ,
    void_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT je_balanced CHECK (status = 'DRAFT' OR ABS(total_debit - total_credit) < 0.01),
    CONSTRAINT uq_je_tenant_number UNIQUE (tenant_id, journal_number),
    CONSTRAINT uq_je_trace UNIQUE (tenant_id, trace_id)
);

CREATE INDEX idx_je_tenant_date ON journal_entries(tenant_id, journal_date);
CREATE INDEX idx_je_source ON journal_entries(tenant_id, source_type, source_id);
CREATE INDEX idx_je_status ON journal_entries(tenant_id, status);

COMMENT ON TABLE journal_entries IS 'Journal entries (header) - append-only';

-- ============================================
-- 4. Journal Lines
-- ============================================
CREATE TABLE journal_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_id UUID NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    account_id UUID NOT NULL REFERENCES chart_of_accounts(id),
    debit DECIMAL(18,2) NOT NULL DEFAULT 0,
    credit DECIMAL(18,2) NOT NULL DEFAULT 0,
    memo TEXT,
    CONSTRAINT jl_has_amount CHECK (debit >= 0 AND credit >= 0 AND (debit > 0 OR credit > 0)),
    CONSTRAINT jl_exclusive CHECK (NOT (debit > 0 AND credit > 0))
);

CREATE INDEX idx_jl_journal ON journal_lines(journal_id);
CREATE INDEX idx_jl_account ON journal_lines(account_id);

COMMENT ON TABLE journal_lines IS 'Journal entry lines (details)';

-- ============================================
-- 5. Accounts Receivable
-- ============================================
CREATE TABLE accounts_receivable (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    customer_id UUID,
    customer_name TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    amount DECIMAL(18,2) NOT NULL,
    amount_paid DECIMAL(18,2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'PARTIAL', 'PAID', 'VOID')),
    description TEXT,
    source_type TEXT,
    source_id UUID,
    currency TEXT DEFAULT 'IDR',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ar_tenant ON accounts_receivable(tenant_id);
CREATE INDEX idx_ar_customer ON accounts_receivable(tenant_id, customer_id);
CREATE INDEX idx_ar_status ON accounts_receivable(tenant_id, status);
CREATE INDEX idx_ar_due ON accounts_receivable(tenant_id, due_date);

COMMENT ON TABLE accounts_receivable IS 'Accounts Receivable (Piutang)';

-- ============================================
-- 6. AR Payment Applications
-- ============================================
CREATE TABLE ar_payment_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    ar_id UUID NOT NULL REFERENCES accounts_receivable(id),
    payment_date DATE NOT NULL,
    amount_applied DECIMAL(18,2) NOT NULL,
    payment_method TEXT NOT NULL,
    reference_number TEXT,
    notes TEXT,
    journal_id UUID REFERENCES journal_entries(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ar_payment_tenant ON ar_payment_applications(tenant_id);
CREATE INDEX idx_ar_payment_ar ON ar_payment_applications(ar_id);

COMMENT ON TABLE ar_payment_applications IS 'AR Payment applications';

-- ============================================
-- 7. Accounts Payable
-- ============================================
CREATE TABLE accounts_payable (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    supplier_id UUID,
    supplier_name TEXT NOT NULL,
    bill_number TEXT NOT NULL,
    bill_date DATE NOT NULL,
    due_date DATE NOT NULL,
    amount DECIMAL(18,2) NOT NULL,
    amount_paid DECIMAL(18,2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'PARTIAL', 'PAID', 'VOID')),
    description TEXT,
    source_type TEXT,
    source_id UUID,
    currency TEXT DEFAULT 'IDR',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ap_tenant ON accounts_payable(tenant_id);
CREATE INDEX idx_ap_supplier ON accounts_payable(tenant_id, supplier_id);
CREATE INDEX idx_ap_status ON accounts_payable(tenant_id, status);
CREATE INDEX idx_ap_due ON accounts_payable(tenant_id, due_date);

COMMENT ON TABLE accounts_payable IS 'Accounts Payable (Hutang)';

-- ============================================
-- 8. AP Payment Applications
-- ============================================
CREATE TABLE ap_payment_applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    ap_id UUID NOT NULL REFERENCES accounts_payable(id),
    payment_date DATE NOT NULL,
    amount_applied DECIMAL(18,2) NOT NULL,
    payment_method TEXT NOT NULL,
    reference_number TEXT,
    notes TEXT,
    journal_id UUID REFERENCES journal_entries(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ap_payment_tenant ON ap_payment_applications(tenant_id);
CREATE INDEX idx_ap_payment_ap ON ap_payment_applications(ap_id);

COMMENT ON TABLE ap_payment_applications IS 'AP Payment applications';

-- ============================================
-- 9. Account Balances Daily (Cache)
-- ============================================
CREATE TABLE account_balances_daily (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES chart_of_accounts(id),
    balance_date DATE NOT NULL,
    debit_balance DECIMAL(18,2) NOT NULL DEFAULT 0,
    credit_balance DECIMAL(18,2) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_balance_daily UNIQUE (tenant_id, account_id, balance_date)
);

CREATE INDEX idx_balance_tenant_date ON account_balances_daily(tenant_id, balance_date);

COMMENT ON TABLE account_balances_daily IS 'Daily account balance cache';

-- ============================================
-- 10. Journal Number Sequences
-- ============================================
CREATE TABLE journal_number_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    prefix TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    last_number INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_journal_seq UNIQUE (tenant_id, prefix, year, month)
);

COMMENT ON TABLE journal_number_sequences IS 'Journal number sequences per tenant/prefix/month';

-- ============================================
-- 11. Accounting Outbox (Events)
-- ============================================
CREATE TABLE accounting_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id UUID NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    error TEXT
);

CREATE INDEX idx_outbox_unprocessed ON accounting_outbox(created_at) WHERE processed_at IS NULL;

COMMENT ON TABLE accounting_outbox IS 'Outbox for reliable event publishing';

-- ============================================
-- 12. Enable RLS
-- ============================================
ALTER TABLE chart_of_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_receivable ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_payable ENABLE ROW LEVEL SECURITY;
ALTER TABLE ar_payment_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE ap_payment_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_balances_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_number_sequences ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_coa ON chart_of_accounts
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_fiscal ON fiscal_periods
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_journal ON journal_entries
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_ar ON accounts_receivable
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_ap ON accounts_payable
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_ar_payment ON ar_payment_applications
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_ap_payment ON ap_payment_applications
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_balances ON account_balances_daily
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_journal_seq ON journal_number_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================
-- 13. Helper Functions
-- ============================================

-- Get next journal number
CREATE OR REPLACE FUNCTION get_next_journal_number(
    p_tenant_id TEXT,
    p_prefix TEXT DEFAULT 'JV'
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_year INTEGER := EXTRACT(YEAR FROM CURRENT_DATE);
    v_month INTEGER := EXTRACT(MONTH FROM CURRENT_DATE);
    v_number INTEGER;
BEGIN
    INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
    VALUES (p_tenant_id, p_prefix, v_year, v_month, 1)
    ON CONFLICT (tenant_id, prefix, year, month)
    DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_number;

    RETURN p_prefix || '-' || to_char(CURRENT_DATE, 'YYMM') || '-' || lpad(v_number::TEXT, 4, '0');
END;
$$;

-- Seed default CoA function
CREATE OR REPLACE FUNCTION seed_default_coa(p_tenant_id TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM "Tenant" WHERE id = p_tenant_id) THEN
        RAISE EXCEPTION 'Tenant not found: %', p_tenant_id;
    END IF;

    IF EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id = p_tenant_id LIMIT 1) THEN
        RAISE NOTICE 'CoA already exists for tenant %, skipping', p_tenant_id;
        RETURN 0;
    END IF;

    PERFORM set_config('app.tenant_id', p_tenant_id, true);

    -- ASSETS
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false);
    v_count := v_count + 14;

    -- LIABILITIES
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-10100', 'Hutang Usaha', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 7;

    -- EQUITY
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '3-10000', 'Modal', 'EQUITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '3-10100', 'Modal Pemilik', 'EQUITY', 'CREDIT', '3-10000', 2, false),
        (p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', NULL, 1, false);
    v_count := v_count + 5;

    -- INCOME
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '4-10000', 'Pendapatan Usaha', 'INCOME', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '4-10100', 'Penjualan', 'INCOME', 'CREDIT', '4-10000', 2, false),
        (p_tenant_id, '4-10200', 'Diskon Penjualan', 'INCOME', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-10300', 'Retur Penjualan', 'INCOME', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-90000', 'Pendapatan Lain-lain', 'INCOME', 'CREDIT', NULL, 1, false);
    v_count := v_count + 5;

    -- EXPENSE
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'EXPENSE', 'DEBIT', '5-10000', 2, false),
        (p_tenant_id, '5-10200', 'Diskon Pembelian', 'EXPENSE', 'CREDIT', '5-10000', 2, false),
        (p_tenant_id, '5-10300', 'Retur Pembelian', 'EXPENSE', 'CREDIT', '5-10000', 2, false),
        (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20500', 'Beban Transportasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20700', 'Beban Pemeliharaan', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-30000', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '5-30100', 'Beban Penyusutan Bangunan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (p_tenant_id, '5-30200', 'Beban Penyusutan Kendaraan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (p_tenant_id, '5-30300', 'Beban Penyusutan Peralatan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (p_tenant_id, '5-80000', 'Beban Pajak', 'EXPENSE', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '5-90000', 'Beban Non-Operasional', 'EXPENSE', 'DEBIT', NULL, 1, false);
    v_count := v_count + 19;

    RAISE NOTICE 'Seeded % accounts for tenant %', v_count, p_tenant_id;
    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION seed_default_coa(TEXT) IS 'Seeds default Chart of Accounts for a tenant';

-- ============================================
-- 14. Seed CoA for existing tenants
-- ============================================
DO $$
DECLARE
    t_id TEXT;
    t_count INTEGER;
BEGIN
    FOR t_id IN SELECT id FROM "Tenant" LOOP
        BEGIN
            t_count := seed_default_coa(t_id);
            RAISE NOTICE 'Seeded CoA for tenant %: % accounts', t_id, t_count;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Error seeding tenant %: %', t_id, SQLERRM;
        END;
    END LOOP;
END;
$$;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'Migration V013: Accounting Kernel Complete - SUCCESS';
    RAISE NOTICE 'Tables: chart_of_accounts, fiscal_periods, journal_entries, journal_lines';
    RAISE NOTICE 'Tables: accounts_receivable, accounts_payable, ar/ap_payment_applications';
    RAISE NOTICE 'Tables: account_balances_daily, journal_number_sequences, accounting_outbox';
    RAISE NOTICE 'RLS enabled, seed_default_coa() function available';
END;
$$;
