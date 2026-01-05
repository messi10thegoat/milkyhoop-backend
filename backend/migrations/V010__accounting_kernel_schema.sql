-- ============================================================================
-- MilkyHoop Accounting Kernel Schema
-- Migration: V010
-- Date: 2026-01-04
-- Description: Create core accounting tables for QuickBooks-like kernel
-- ============================================================================

-- ============================================================================
-- 1. CHART OF ACCOUNTS (CoA)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chart_of_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    code            VARCHAR(20) NOT NULL,
    name            VARCHAR(100) NOT NULL,
    type            VARCHAR(20) NOT NULL,
    normal_balance  VARCHAR(10) NOT NULL,
    parent_id       UUID REFERENCES chart_of_accounts(id),
    is_active       BOOLEAN DEFAULT true,
    is_system       BOOLEAN DEFAULT false,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_coa_tenant_code UNIQUE(tenant_id, code),
    CONSTRAINT chk_coa_type CHECK (type IN ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE')),
    CONSTRAINT chk_coa_normal_balance CHECK (normal_balance IN ('DEBIT', 'CREDIT'))
);

CREATE INDEX IF NOT EXISTS idx_coa_tenant ON chart_of_accounts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_coa_type ON chart_of_accounts(tenant_id, type);
CREATE INDEX IF NOT EXISTS idx_coa_parent ON chart_of_accounts(parent_id);
CREATE INDEX IF NOT EXISTS idx_coa_active ON chart_of_accounts(tenant_id, is_active) WHERE is_active = true;

COMMENT ON TABLE chart_of_accounts IS 'Bagan Akun - Chart of Accounts dengan hierarki';
COMMENT ON COLUMN chart_of_accounts.code IS 'Kode akun unik per tenant (e.g., 1-10100)';
COMMENT ON COLUMN chart_of_accounts.type IS 'Kategori: ASSET, LIABILITY, EQUITY, INCOME, EXPENSE';
COMMENT ON COLUMN chart_of_accounts.normal_balance IS 'Saldo normal: DEBIT atau CREDIT';
COMMENT ON COLUMN chart_of_accounts.is_system IS 'Akun sistem yang tidak bisa dihapus';


-- ============================================================================
-- 2. FISCAL PERIODS
-- ============================================================================

CREATE TABLE IF NOT EXISTS fiscal_periods (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL,
    period_name         VARCHAR(20) NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    is_closed           BOOLEAN DEFAULT false,
    closed_at           TIMESTAMPTZ,
    closed_by           UUID,
    closing_journal_id  UUID,
    opening_balances    JSONB,
    closing_balances    JSONB,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_fiscal_tenant_period UNIQUE(tenant_id, period_name)
);

CREATE INDEX IF NOT EXISTS idx_fiscal_tenant ON fiscal_periods(tenant_id);
CREATE INDEX IF NOT EXISTS idx_fiscal_dates ON fiscal_periods(tenant_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_fiscal_closed ON fiscal_periods(tenant_id, is_closed);

COMMENT ON TABLE fiscal_periods IS 'Periode fiskal untuk tutup buku';
COMMENT ON COLUMN fiscal_periods.period_name IS 'Nama periode (e.g., 2026-01)';
COMMENT ON COLUMN fiscal_periods.is_closed IS 'True jika periode sudah ditutup';


-- ============================================================================
-- 3. JOURNAL ENTRIES (Header) - Partitioned by journal_date
-- ============================================================================

CREATE TABLE IF NOT EXISTS journal_entries (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    journal_number  VARCHAR(50) NOT NULL,
    journal_date    DATE NOT NULL,
    description     TEXT,

    -- Source tracking
    source_type     VARCHAR(30) NOT NULL,
    source_id       UUID,
    trace_id        UUID NOT NULL,
    source_snapshot JSONB,

    -- Status
    status          VARCHAR(20) DEFAULT 'POSTED',
    voided_by       UUID,
    void_reason     TEXT,

    -- Audit
    posted_at       TIMESTAMPTZ,
    posted_by       UUID,
    created_at      TIMESTAMPTZ DEFAULT now(),
    version         INT DEFAULT 1,

    PRIMARY KEY (id, journal_date),
    CONSTRAINT chk_journal_status CHECK (status IN ('DRAFT', 'POSTED', 'VOID'))
) PARTITION BY RANGE (journal_date);

-- Create unique constraint for idempotency (must include partition key)
CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_idempotency
    ON journal_entries(tenant_id, trace_id, journal_date);

CREATE INDEX IF NOT EXISTS idx_journal_tenant_date ON journal_entries(tenant_id, journal_date);
CREATE INDEX IF NOT EXISTS idx_journal_source ON journal_entries(tenant_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_journal_status ON journal_entries(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_journal_number ON journal_entries(tenant_id, journal_number);

COMMENT ON TABLE journal_entries IS 'Jurnal Umum - Header entry (partitioned by date)';
COMMENT ON COLUMN journal_entries.trace_id IS 'Idempotency key untuk exactly-once semantics';
COMMENT ON COLUMN journal_entries.source_type IS 'INVOICE, BILL, PAYMENT, POS, ADJUSTMENT, MANUAL';
COMMENT ON COLUMN journal_entries.source_snapshot IS 'Full payload dari source untuk audit';


-- ============================================================================
-- 4. CREATE JOURNAL PARTITIONS (2025-2026)
-- ============================================================================

-- 2025 Partitions (for historical data if any)
CREATE TABLE IF NOT EXISTS journal_entries_2025_01 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_02 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_03 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_04 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_05 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_06 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_07 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_08 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_09 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_10 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_11 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE IF NOT EXISTS journal_entries_2025_12 PARTITION OF journal_entries
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');

-- 2026 Partitions
CREATE TABLE IF NOT EXISTS journal_entries_2026_01 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_02 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_03 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_04 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_05 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_06 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_07 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_08 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_09 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_10 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_11 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS journal_entries_2026_12 PARTITION OF journal_entries
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');


-- ============================================================================
-- 5. JOURNAL LINES (Detail)
-- ============================================================================

CREATE TABLE IF NOT EXISTS journal_lines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_id      UUID NOT NULL,
    journal_date    DATE NOT NULL,
    account_id      UUID NOT NULL REFERENCES chart_of_accounts(id),
    line_number     INT NOT NULL,
    description     TEXT,
    debit           DECIMAL(24,6) DEFAULT 0,
    credit          DECIMAL(24,6) DEFAULT 0,

    -- Optional dimensions
    department_id   UUID,
    project_id      UUID,

    -- Multi-currency (future)
    currency        CHAR(3) DEFAULT 'IDR',
    exchange_rate   DECIMAL(18,8) DEFAULT 1,
    amount_local    DECIMAL(24,6),

    created_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT fk_journal_lines_entry FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date) ON DELETE CASCADE,
    CONSTRAINT chk_debit_credit CHECK (
        (debit >= 0 AND credit >= 0) AND
        ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0) OR (debit = 0 AND credit = 0))
    )
);

CREATE INDEX IF NOT EXISTS idx_jlines_journal ON journal_lines(journal_id);
CREATE INDEX IF NOT EXISTS idx_jlines_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_jlines_account_date ON journal_lines(account_id, journal_date);
CREATE INDEX IF NOT EXISTS idx_jlines_date ON journal_lines(journal_date);

COMMENT ON TABLE journal_lines IS 'Detail jurnal - setiap baris debit/credit';
COMMENT ON COLUMN journal_lines.line_number IS 'Urutan baris dalam jurnal';


-- ============================================================================
-- 6. ACCOUNTS RECEIVABLE (AR Subledger)
-- ============================================================================

CREATE TABLE IF NOT EXISTS accounts_receivable (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    customer_id     UUID NOT NULL,
    customer_name   VARCHAR(255),

    -- Source
    source_type     VARCHAR(30) NOT NULL,
    source_id       UUID NOT NULL,
    source_number   VARCHAR(50),

    -- Amount
    amount          DECIMAL(24,6) NOT NULL,
    balance         DECIMAL(24,6) NOT NULL,
    currency        CHAR(3) DEFAULT 'IDR',

    -- Dates
    issue_date      DATE NOT NULL,
    due_date        DATE NOT NULL,

    -- Status
    status          VARCHAR(20) DEFAULT 'OPEN',

    -- Link to journal
    journal_id      UUID,
    journal_date    DATE,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT fk_ar_journal FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date),
    CONSTRAINT chk_ar_status CHECK (status IN ('OPEN', 'PARTIAL', 'PAID', 'VOID'))
);

CREATE INDEX IF NOT EXISTS idx_ar_tenant_customer ON accounts_receivable(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_ar_status ON accounts_receivable(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ar_due_date ON accounts_receivable(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_ar_source ON accounts_receivable(tenant_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_ar_open ON accounts_receivable(tenant_id, status, due_date)
    WHERE status IN ('OPEN', 'PARTIAL');

COMMENT ON TABLE accounts_receivable IS 'Piutang Usaha - AR Subledger';
COMMENT ON COLUMN accounts_receivable.balance IS 'Sisa yang belum dibayar';


-- ============================================================================
-- 7. ACCOUNTS PAYABLE (AP Subledger)
-- ============================================================================

CREATE TABLE IF NOT EXISTS accounts_payable (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    supplier_id     UUID NOT NULL,
    supplier_name   VARCHAR(255),

    -- Source
    source_type     VARCHAR(30) NOT NULL,
    source_id       UUID NOT NULL,
    source_number   VARCHAR(50),

    -- Amount
    amount          DECIMAL(24,6) NOT NULL,
    balance         DECIMAL(24,6) NOT NULL,
    currency        CHAR(3) DEFAULT 'IDR',

    -- Dates
    issue_date      DATE NOT NULL,
    due_date        DATE NOT NULL,

    -- Status
    status          VARCHAR(20) DEFAULT 'OPEN',

    -- Link to journal
    journal_id      UUID,
    journal_date    DATE,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT fk_ap_journal FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date),
    CONSTRAINT chk_ap_status CHECK (status IN ('OPEN', 'PARTIAL', 'PAID', 'VOID'))
);

CREATE INDEX IF NOT EXISTS idx_ap_tenant_supplier ON accounts_payable(tenant_id, supplier_id);
CREATE INDEX IF NOT EXISTS idx_ap_status ON accounts_payable(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ap_due_date ON accounts_payable(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_ap_source ON accounts_payable(tenant_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_ap_open ON accounts_payable(tenant_id, status, due_date)
    WHERE status IN ('OPEN', 'PARTIAL');

COMMENT ON TABLE accounts_payable IS 'Hutang Usaha - AP Subledger';


-- ============================================================================
-- 8. AR PAYMENT APPLICATIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS ar_payment_applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    ar_id           UUID NOT NULL REFERENCES accounts_receivable(id),
    payment_id      UUID NOT NULL,
    payment_date    DATE NOT NULL,
    amount_applied  DECIMAL(24,6) NOT NULL,
    journal_id      UUID,
    journal_date    DATE,
    created_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT fk_ar_payment_journal FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date)
);

CREATE INDEX IF NOT EXISTS idx_ar_payment_ar ON ar_payment_applications(ar_id);
CREATE INDEX IF NOT EXISTS idx_ar_payment_payment ON ar_payment_applications(payment_id);

COMMENT ON TABLE ar_payment_applications IS 'Aplikasi pembayaran ke piutang';


-- ============================================================================
-- 9. AP PAYMENT APPLICATIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS ap_payment_applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    ap_id           UUID NOT NULL REFERENCES accounts_payable(id),
    payment_id      UUID NOT NULL,
    payment_date    DATE NOT NULL,
    amount_applied  DECIMAL(24,6) NOT NULL,
    journal_id      UUID,
    journal_date    DATE,
    created_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT fk_ap_payment_journal FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date)
);

CREATE INDEX IF NOT EXISTS idx_ap_payment_ap ON ap_payment_applications(ap_id);
CREATE INDEX IF NOT EXISTS idx_ap_payment_payment ON ap_payment_applications(payment_id);

COMMENT ON TABLE ap_payment_applications IS 'Aplikasi pembayaran ke hutang';


-- ============================================================================
-- 10. ACCOUNT BALANCES (Materialized Read Model)
-- ============================================================================

CREATE TABLE IF NOT EXISTS account_balances_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    account_id      UUID NOT NULL REFERENCES chart_of_accounts(id),
    balance_date    DATE NOT NULL,

    -- Running balances
    opening_debit   DECIMAL(24,6) DEFAULT 0,
    opening_credit  DECIMAL(24,6) DEFAULT 0,
    period_debit    DECIMAL(24,6) DEFAULT 0,
    period_credit   DECIMAL(24,6) DEFAULT 0,
    closing_debit   DECIMAL(24,6) DEFAULT 0,
    closing_credit  DECIMAL(24,6) DEFAULT 0,

    -- Net balance (computed based on normal balance)
    net_balance     DECIMAL(24,6) DEFAULT 0,

    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_balance_tenant_account_date UNIQUE(tenant_id, account_id, balance_date)
);

CREATE INDEX IF NOT EXISTS idx_balances_tenant_date ON account_balances_daily(tenant_id, balance_date);
CREATE INDEX IF NOT EXISTS idx_balances_account ON account_balances_daily(account_id, balance_date);

COMMENT ON TABLE account_balances_daily IS 'Cache saldo harian per akun (untuk report cepat)';


-- ============================================================================
-- 11. ACCOUNTING OUTBOX (Event Publishing)
-- ============================================================================

CREATE TABLE IF NOT EXISTS accounting_outbox (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    event_type      VARCHAR(100) NOT NULL,
    event_key       VARCHAR(100),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    published_at    TIMESTAMPTZ,
    is_published    BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_acc_outbox_unpublished ON accounting_outbox(is_published, created_at)
    WHERE is_published = false;

COMMENT ON TABLE accounting_outbox IS 'Outbox pattern untuk publish event ke Kafka';


-- ============================================================================
-- 12. JOURNAL NUMBER SEQUENCES (Per Tenant)
-- ============================================================================

CREATE TABLE IF NOT EXISTS journal_number_sequences (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    prefix          VARCHAR(20) NOT NULL DEFAULT 'JV',
    year            INT NOT NULL,
    last_number     INT NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_journal_seq UNIQUE(tenant_id, prefix, year)
);

COMMENT ON TABLE journal_number_sequences IS 'Sequence untuk nomor jurnal per tenant per tahun';


-- ============================================================================
-- 13. ROW LEVEL SECURITY (Tenant Isolation)
-- ============================================================================

-- Enable RLS on all accounting tables
ALTER TABLE chart_of_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_receivable ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_payable ENABLE ROW LEVEL SECURITY;
ALTER TABLE ar_payment_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE ap_payment_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_balances_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_number_sequences ENABLE ROW LEVEL SECURITY;

-- Create RLS policies (using session variable app.tenant_id)
CREATE POLICY rls_coa ON chart_of_accounts
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_fiscal ON fiscal_periods
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_journal ON journal_entries
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_ar ON accounts_receivable
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_ap ON accounts_payable
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_ar_payment ON ar_payment_applications
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_ap_payment ON ap_payment_applications
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_balances ON account_balances_daily
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_acc_outbox ON accounting_outbox
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));

CREATE POLICY rls_journal_seq ON journal_number_sequences
    FOR ALL USING (tenant_id::text = current_setting('app.tenant_id', true));


-- ============================================================================
-- 14. HELPER FUNCTIONS
-- ============================================================================

-- Function to get next journal number
CREATE OR REPLACE FUNCTION get_next_journal_number(
    p_tenant_id UUID,
    p_prefix VARCHAR DEFAULT 'JV',
    p_year INT DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INT
) RETURNS VARCHAR AS $$
DECLARE
    v_next_num INT;
    v_journal_number VARCHAR;
BEGIN
    -- Insert or update sequence atomically
    INSERT INTO journal_number_sequences (tenant_id, prefix, year, last_number)
    VALUES (p_tenant_id, p_prefix, p_year, 1)
    ON CONFLICT (tenant_id, prefix, year)
    DO UPDATE SET
        last_number = journal_number_sequences.last_number + 1,
        updated_at = NOW()
    RETURNING last_number INTO v_next_num;

    -- Format: JV-2026-0001
    v_journal_number := p_prefix || '-' || p_year::TEXT || '-' || LPAD(v_next_num::TEXT, 4, '0');

    RETURN v_journal_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_next_journal_number IS 'Generate nomor jurnal sequential per tenant per tahun';


-- Function to validate double-entry for a journal
CREATE OR REPLACE FUNCTION validate_journal_double_entry(p_journal_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    v_total_debit DECIMAL(24,6);
    v_total_credit DECIMAL(24,6);
BEGIN
    SELECT
        COALESCE(SUM(debit), 0),
        COALESCE(SUM(credit), 0)
    INTO v_total_debit, v_total_credit
    FROM journal_lines
    WHERE journal_id = p_journal_id;

    -- Check if balanced (with small tolerance for floating point)
    RETURN ABS(v_total_debit - v_total_credit) < 0.01;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION validate_journal_double_entry IS 'Validasi double-entry: total debit = total credit';


-- Function to update account balances after journal posted
CREATE OR REPLACE FUNCTION update_account_balances_for_journal(
    p_journal_id UUID,
    p_journal_date DATE,
    p_tenant_id UUID
) RETURNS VOID AS $$
BEGIN
    -- Upsert daily balances for each affected account
    INSERT INTO account_balances_daily (
        tenant_id,
        account_id,
        balance_date,
        period_debit,
        period_credit,
        closing_debit,
        closing_credit,
        net_balance,
        updated_at
    )
    SELECT
        p_tenant_id,
        jl.account_id,
        p_journal_date,
        COALESCE(SUM(jl.debit), 0),
        COALESCE(SUM(jl.credit), 0),
        COALESCE(SUM(jl.debit), 0),
        COALESCE(SUM(jl.credit), 0),
        CASE
            WHEN c.normal_balance = 'DEBIT'
            THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
            ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
        END,
        NOW()
    FROM journal_lines jl
    JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE jl.journal_id = p_journal_id
    GROUP BY jl.account_id, c.normal_balance
    ON CONFLICT (tenant_id, account_id, balance_date)
    DO UPDATE SET
        period_debit = account_balances_daily.period_debit + EXCLUDED.period_debit,
        period_credit = account_balances_daily.period_credit + EXCLUDED.period_credit,
        closing_debit = account_balances_daily.closing_debit + EXCLUDED.period_debit,
        closing_credit = account_balances_daily.closing_credit + EXCLUDED.period_credit,
        net_balance = account_balances_daily.net_balance + EXCLUDED.net_balance,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_account_balances_for_journal IS 'Update cache saldo setelah jurnal diposting';


-- ============================================================================
-- 15. TRIGGERS
-- ============================================================================

-- Trigger to validate double-entry before journal insert
CREATE OR REPLACE FUNCTION trigger_validate_journal()
RETURNS TRIGGER AS $$
BEGIN
    -- Only validate on POSTED status
    IF NEW.status = 'POSTED' THEN
        IF NOT validate_journal_double_entry(NEW.id) THEN
            RAISE EXCEPTION 'Journal % tidak balance: total debit != total credit', NEW.journal_number;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Note: Trigger disabled by default karena lines di-insert setelah header
-- Validasi dilakukan di application layer sebelum commit

-- Trigger untuk update timestamp
CREATE OR REPLACE FUNCTION trigger_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_coa_updated_at
    BEFORE UPDATE ON chart_of_accounts
    FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();

CREATE TRIGGER trg_fiscal_updated_at
    BEFORE UPDATE ON fiscal_periods
    FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();

CREATE TRIGGER trg_ar_updated_at
    BEFORE UPDATE ON accounts_receivable
    FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();

CREATE TRIGGER trg_ap_updated_at
    BEFORE UPDATE ON accounts_payable
    FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();


-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

-- Log migration
DO $$
BEGIN
    RAISE NOTICE 'Migration V010: Accounting Kernel Schema created successfully';
    RAISE NOTICE 'Tables created: chart_of_accounts, fiscal_periods, journal_entries, journal_lines';
    RAISE NOTICE 'Tables created: accounts_receivable, accounts_payable, ar/ap_payment_applications';
    RAISE NOTICE 'Tables created: account_balances_daily, accounting_outbox, journal_number_sequences';
    RAISE NOTICE 'Journal partitions: 2025-01 to 2026-12';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
