-- =============================================================================
-- Migration V012: Fix Accounting Kernel tenant_id Type
-- =============================================================================
-- The existing system uses TEXT for tenant_id (e.g., 'evlogia', 'bca')
-- This migration changes all accounting tables to use TEXT instead of UUID
-- =============================================================================

-- Step 1: Drop RLS policies (they depend on tenant_id column)
DROP POLICY IF EXISTS rls_coa ON chart_of_accounts;
DROP POLICY IF EXISTS rls_fiscal ON fiscal_periods;
DROP POLICY IF EXISTS rls_journal ON journal_entries;
DROP POLICY IF EXISTS rls_ar ON accounts_receivable;
DROP POLICY IF EXISTS rls_ap ON accounts_payable;
DROP POLICY IF EXISTS rls_ar_payment ON ar_payment_applications;
DROP POLICY IF EXISTS rls_ap_payment ON ap_payment_applications;
DROP POLICY IF EXISTS rls_balances ON account_balances_daily;
DROP POLICY IF EXISTS rls_acc_outbox ON accounting_outbox;
DROP POLICY IF EXISTS rls_journal_seq ON journal_number_sequences;

-- Step 2: Drop triggers that might reference the columns
DROP TRIGGER IF EXISTS trg_journal_balances ON journal_entries;
DROP TRIGGER IF EXISTS trg_ar_outbox ON accounts_receivable;
DROP TRIGGER IF EXISTS trg_ap_outbox ON accounts_payable;
DROP TRIGGER IF EXISTS trg_journal_outbox ON journal_entries;

-- Step 3: Change tenant_id columns from UUID to TEXT
ALTER TABLE chart_of_accounts ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE fiscal_periods ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE accounts_receivable ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE accounts_payable ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE ar_payment_applications ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE ap_payment_applications ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE account_balances_daily ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE accounting_outbox ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;
ALTER TABLE journal_number_sequences ALTER COLUMN tenant_id TYPE TEXT USING tenant_id::TEXT;

-- For partitioned table journal_entries, we need to drop and recreate
-- First, drop all child partitions
DO $$
DECLARE
    partition_name TEXT;
BEGIN
    FOR partition_name IN
        SELECT inhrelid::regclass::TEXT
        FROM pg_inherits
        WHERE inhparent = 'journal_entries'::regclass
    LOOP
        EXECUTE 'DROP TABLE IF EXISTS ' || partition_name || ' CASCADE';
    END LOOP;
END;
$$;

-- Drop the parent table
DROP TABLE IF EXISTS journal_entries CASCADE;

-- Recreate journal_entries with TEXT tenant_id
CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    journal_number TEXT NOT NULL,
    journal_date DATE NOT NULL,
    description TEXT NOT NULL,
    source_type TEXT,
    source_id UUID,
    trace_id TEXT,
    status TEXT NOT NULL DEFAULT 'POSTED',
    total_debit DECIMAL(18,2) NOT NULL DEFAULT 0,
    total_credit DECIMAL(18,2) NOT NULL DEFAULT 0,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT je_balanced CHECK (ABS(total_debit - total_credit) < 0.01)
) PARTITION BY RANGE (journal_date);

CREATE INDEX idx_je_tenant_date ON journal_entries(tenant_id, journal_date);
CREATE INDEX idx_je_number ON journal_entries(tenant_id, journal_number);
CREATE INDEX idx_je_source ON journal_entries(tenant_id, source_type, source_id);
CREATE INDEX idx_je_trace ON journal_entries(tenant_id, trace_id);

COMMENT ON TABLE journal_entries IS 'Journal entries (header) - partitioned by month';

-- Drop and recreate journal_lines
DROP TABLE IF EXISTS journal_lines CASCADE;

CREATE TABLE journal_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_id UUID NOT NULL,
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

COMMENT ON TABLE journal_lines IS 'Journal entry lines (details) - debit or credit per account';

-- Create partitions for journal_entries (2025-2026)
DO $$
DECLARE
    y INT;
    m INT;
    start_date DATE;
    end_date DATE;
    partition_name TEXT;
BEGIN
    FOR y IN 2025..2026 LOOP
        FOR m IN 1..12 LOOP
            start_date := make_date(y, m, 1);
            end_date := (start_date + INTERVAL '1 month')::DATE;
            partition_name := 'journal_entries_' || to_char(start_date, 'YYYY_MM');

            EXECUTE format(
                'CREATE TABLE %I PARTITION OF journal_entries FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                start_date,
                end_date
            );
        END LOOP;
    END LOOP;
END;
$$;

-- Step 4: Add foreign key constraints to Tenant table
ALTER TABLE chart_of_accounts
    ADD CONSTRAINT chart_of_accounts_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE fiscal_periods
    ADD CONSTRAINT fiscal_periods_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE accounts_receivable
    ADD CONSTRAINT accounts_receivable_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE accounts_payable
    ADD CONSTRAINT accounts_payable_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE ar_payment_applications
    ADD CONSTRAINT ar_payment_applications_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE ap_payment_applications
    ADD CONSTRAINT ap_payment_applications_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE account_balances_daily
    ADD CONSTRAINT account_balances_daily_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

ALTER TABLE journal_number_sequences
    ADD CONSTRAINT journal_number_sequences_tenant_id_fkey
    FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE;

-- Step 5: Recreate RLS policies
ALTER TABLE chart_of_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_receivable ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_payable ENABLE ROW LEVEL SECURITY;
ALTER TABLE ar_payment_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE ap_payment_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_balances_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_outbox ENABLE ROW LEVEL SECURITY;
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

CREATE POLICY rls_acc_outbox ON accounting_outbox
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_journal_seq ON journal_number_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- Step 6: Update seed function to use TEXT
CREATE OR REPLACE FUNCTION seed_default_coa(p_tenant_id TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER := 0;
BEGIN
    -- Check if tenant exists
    IF NOT EXISTS (SELECT 1 FROM "Tenant" WHERE id = p_tenant_id) THEN
        RAISE EXCEPTION 'Tenant not found: %', p_tenant_id;
    END IF;

    -- Check if already seeded
    IF EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id = p_tenant_id LIMIT 1) THEN
        RAISE NOTICE 'CoA already exists for tenant %, skipping', p_tenant_id;
        RETURN 0;
    END IF;

    -- Set tenant context for RLS
    PERFORM set_config('app.tenant_id', p_tenant_id, true);

    -- =============================
    -- 1. ASET (ASSETS)
    -- =============================
    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10400', 'Piutang Usaha', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-10800', 'Uang Muka Pembelian', 'ASSET', 'DEBIT', '1-10000', 2, false);
    v_count := v_count + 9;

    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-20500', 'Inventaris Kantor', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false);
    v_count := v_count + 7;

    -- =============================
    -- 2. LIABILITAS (LIABILITIES)
    -- =============================
    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '2-10100', 'Hutang Usaha', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '2-10500', 'Pendapatan Diterima Dimuka', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '2-10600', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false);
    v_count := v_count + 7;

    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '2-20200', 'Hutang Pihak Ketiga', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 3;

    -- =============================
    -- 3. EKUITAS (EQUITY)
    -- =============================
    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '3-10000', 'Modal', 'EQUITY', 'CREDIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '3-10100', 'Modal Pemilik', 'EQUITY', 'CREDIT', '3-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '3-10200', 'Modal Disetor', 'EQUITY', 'CREDIT', '3-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (gen_random_uuid(), p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (gen_random_uuid(), p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', NULL, 1, false);
    v_count := v_count + 6;

    -- =============================
    -- 4. PENDAPATAN (INCOME)
    -- =============================
    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '4-10000', 'Pendapatan Usaha', 'INCOME', 'CREDIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '4-10100', 'Penjualan', 'INCOME', 'CREDIT', '4-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '4-10200', 'Diskon Penjualan', 'INCOME', 'DEBIT', '4-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '4-10300', 'Retur Penjualan', 'INCOME', 'DEBIT', '4-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '4-90000', 'Pendapatan Lain-lain', 'INCOME', 'CREDIT', NULL, 1, false);
    v_count := v_count + 5;

    -- =============================
    -- 5. BEBAN (EXPENSE)
    -- =============================
    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'EXPENSE', 'DEBIT', '5-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-10200', 'Diskon Pembelian', 'EXPENSE', 'CREDIT', '5-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-10300', 'Retur Pembelian', 'EXPENSE', 'CREDIT', '5-10000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-10400', 'Biaya Angkut Pembelian', 'EXPENSE', 'DEBIT', '5-10000', 2, false);
    v_count := v_count + 5;

    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20500', 'Beban Transportasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20700', 'Beban Pemeliharaan', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20800', 'Beban Administrasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', '5-20000', 2, false);
    v_count := v_count + 10;

    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '5-30000', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (gen_random_uuid(), p_tenant_id, '5-30100', 'Beban Penyusutan Bangunan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-30200', 'Beban Penyusutan Kendaraan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (gen_random_uuid(), p_tenant_id, '5-30300', 'Beban Penyusutan Peralatan', 'EXPENSE', 'DEBIT', '5-30000', 2, false);
    v_count := v_count + 4;

    INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
    VALUES
        (gen_random_uuid(), p_tenant_id, '5-90000', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', NULL, 1, false),
        (gen_random_uuid(), p_tenant_id, '5-80000', 'Beban Pajak', 'EXPENSE', 'DEBIT', NULL, 1, false);
    v_count := v_count + 2;

    RAISE NOTICE 'Seeded % accounts for tenant %', v_count, p_tenant_id;
    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION seed_default_coa(TEXT) IS
'Seeds default Chart of Accounts for a tenant (TEXT tenant_id).';

-- Step 7: Seed for existing tenants
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

-- Log migration complete
DO $$
BEGIN
    RAISE NOTICE 'Migration V012: Fixed tenant_id type to TEXT - COMPLETE';
    RAISE NOTICE 'All accounting tables now use TEXT tenant_id to match Tenant.id';
END;
$$;
