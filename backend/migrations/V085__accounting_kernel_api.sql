-- ===========================================
-- V085: Accounting Kernel API Support
-- Adds fiscal_years, trial_balance_snapshots, and tenant config
-- ===========================================

-- ===========================================
-- 1. FISCAL YEARS TABLE
-- ===========================================
CREATE TABLE IF NOT EXISTS fiscal_years (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_month INT NOT NULL DEFAULT 1,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_at TIMESTAMPTZ,
    closed_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_fiscal_year_tenant_dates UNIQUE (tenant_id, start_date),
    CONSTRAINT chk_fiscal_year_status CHECK (status IN ('open', 'closed')),
    CONSTRAINT chk_fiscal_year_start_month CHECK (start_month BETWEEN 1 AND 12)
);

CREATE INDEX IF NOT EXISTS idx_fiscal_years_tenant ON fiscal_years(tenant_id);
CREATE INDEX IF NOT EXISTS idx_fiscal_years_status ON fiscal_years(tenant_id, status);

-- Enable RLS
ALTER TABLE fiscal_years ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fiscal_years_tenant_isolation ON fiscal_years;
CREATE POLICY fiscal_years_tenant_isolation ON fiscal_years
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ===========================================
-- 2. UPDATE FISCAL_PERIODS (add FK to fiscal_years)
-- ===========================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'fiscal_periods' AND column_name = 'fiscal_year_id'
    ) THEN
        ALTER TABLE fiscal_periods ADD COLUMN fiscal_year_id UUID REFERENCES fiscal_years(id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'fiscal_periods' AND column_name = 'period_number'
    ) THEN
        ALTER TABLE fiscal_periods ADD COLUMN period_number INT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_fiscal_periods_year ON fiscal_periods(fiscal_year_id);

-- ===========================================
-- 3. TRIAL BALANCE SNAPSHOTS
-- ===========================================
CREATE TABLE IF NOT EXISTS trial_balance_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    period_id UUID NOT NULL REFERENCES fiscal_periods(id),
    as_of_date DATE NOT NULL,
    snapshot_type TEXT NOT NULL DEFAULT 'closing',

    lines JSONB NOT NULL,
    total_debit DECIMAL(18,2) NOT NULL,
    total_credit DECIMAL(18,2) NOT NULL,
    is_balanced BOOLEAN NOT NULL,

    generated_at TIMESTAMPTZ DEFAULT NOW(),
    generated_by UUID,

    CONSTRAINT chk_tb_snapshot_type CHECK (snapshot_type IN ('working', 'closing', 'adjusted'))
);

-- Unique constraint (use DO block to handle if exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_tb_snapshot_period_type'
    ) THEN
        ALTER TABLE trial_balance_snapshots
            ADD CONSTRAINT uq_tb_snapshot_period_type UNIQUE (tenant_id, period_id, snapshot_type);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_tb_snapshots_tenant ON trial_balance_snapshots(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tb_snapshots_period ON trial_balance_snapshots(period_id);

-- Enable RLS
ALTER TABLE trial_balance_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tb_snapshots_tenant_isolation ON trial_balance_snapshots;
CREATE POLICY tb_snapshots_tenant_isolation ON trial_balance_snapshots
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ===========================================
-- 4. UPDATE ACCOUNTING SETTINGS (tenant config)
-- ===========================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'journal_approval_required'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN journal_approval_required BOOLEAN DEFAULT FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'strict_period_locking'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN strict_period_locking BOOLEAN DEFAULT FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'allow_period_reopen'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN allow_period_reopen BOOLEAN DEFAULT TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'require_closing_notes'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN require_closing_notes BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

-- ===========================================
-- 5. HELPER FUNCTIONS
-- ===========================================

-- Get current open period for a tenant
CREATE OR REPLACE FUNCTION get_current_open_period(p_tenant_id TEXT)
RETURNS TABLE (
    id UUID,
    period_name TEXT,
    start_date DATE,
    end_date DATE,
    status TEXT,
    fiscal_year_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT fp.id, fp.period_name, fp.start_date, fp.end_date, fp.status, fp.fiscal_year_id
    FROM fiscal_periods fp
    WHERE fp.tenant_id = p_tenant_id
      AND fp.status = 'OPEN'
      AND CURRENT_DATE BETWEEN fp.start_date AND fp.end_date
    ORDER BY fp.start_date DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- Check if can close period (returns validation result)
CREATE OR REPLACE FUNCTION validate_period_close(
    p_tenant_id TEXT,
    p_period_id UUID
) RETURNS TABLE (
    can_close BOOLEAN,
    error_code TEXT,
    error_message TEXT,
    draft_count INT
) AS $$
DECLARE
    v_period RECORD;
    v_prev_period RECORD;
    v_draft_count INT;
    v_strict_mode BOOLEAN;
BEGIN
    -- Get period info
    SELECT * INTO v_period
    FROM fiscal_periods
    WHERE id = p_period_id AND tenant_id = p_tenant_id;

    IF v_period IS NULL THEN
        RETURN QUERY SELECT FALSE, 'PERIOD_NOT_FOUND'::TEXT, 'Period not found'::TEXT, 0;
        RETURN;
    END IF;

    IF v_period.status = 'CLOSED' THEN
        RETURN QUERY SELECT FALSE, 'PERIOD_ALREADY_CLOSED'::TEXT, 'Period is already closed'::TEXT, 0;
        RETURN;
    END IF;

    IF v_period.status = 'LOCKED' THEN
        RETURN QUERY SELECT FALSE, 'PERIOD_LOCKED'::TEXT, 'Period is locked and cannot be modified'::TEXT, 0;
        RETURN;
    END IF;

    -- Check previous period (must be closed for sequential closing)
    SELECT * INTO v_prev_period
    FROM fiscal_periods
    WHERE tenant_id = p_tenant_id
      AND end_date < v_period.start_date
    ORDER BY end_date DESC
    LIMIT 1;

    IF v_prev_period IS NOT NULL AND v_prev_period.status NOT IN ('CLOSED', 'LOCKED') THEN
        RETURN QUERY SELECT FALSE, 'PREVIOUS_PERIOD_OPEN'::TEXT,
            'Previous period (' || v_prev_period.period_name || ') must be closed first'::TEXT, 0;
        RETURN;
    END IF;

    -- Count draft journals in this period
    SELECT COUNT(*) INTO v_draft_count
    FROM journal_entries
    WHERE tenant_id = p_tenant_id
      AND journal_date BETWEEN v_period.start_date AND v_period.end_date
      AND status = 'DRAFT';

    -- Get strict mode setting
    SELECT COALESCE(strict_period_locking, FALSE) INTO v_strict_mode
    FROM accounting_settings
    WHERE tenant_id = p_tenant_id;

    IF v_draft_count > 0 AND v_strict_mode THEN
        RETURN QUERY SELECT FALSE, 'DRAFT_JOURNALS_EXIST'::TEXT,
            v_draft_count || ' draft journal(s) must be posted or deleted before closing'::TEXT, v_draft_count;
        RETURN;
    END IF;

    -- Can close (might have warning if drafts exist but not strict mode)
    IF v_draft_count > 0 THEN
        RETURN QUERY SELECT TRUE, 'WARNING_DRAFT_EXISTS'::TEXT,
            v_draft_count || ' draft journal(s) exist - use force=true to close anyway'::TEXT, v_draft_count;
    ELSE
        RETURN QUERY SELECT TRUE, NULL::TEXT, NULL::TEXT, 0;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

-- Create fiscal year with 12 periods
CREATE OR REPLACE FUNCTION create_fiscal_year_with_periods(
    p_tenant_id TEXT,
    p_name TEXT,
    p_start_month INT,
    p_year INT,
    p_created_by UUID DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    v_fiscal_year_id UUID;
    v_start_date DATE;
    v_end_date DATE;
    v_period_start DATE;
    v_period_end DATE;
    v_period_name TEXT;
    i INT;
BEGIN
    -- Calculate fiscal year dates
    v_start_date := make_date(p_year, p_start_month, 1);
    v_end_date := (v_start_date + INTERVAL '1 year' - INTERVAL '1 day')::DATE;

    -- Check for overlapping fiscal year
    IF EXISTS (
        SELECT 1 FROM fiscal_years
        WHERE tenant_id = p_tenant_id
          AND (
              (v_start_date BETWEEN start_date AND end_date) OR
              (v_end_date BETWEEN start_date AND end_date) OR
              (start_date BETWEEN v_start_date AND v_end_date)
          )
    ) THEN
        RAISE EXCEPTION 'Fiscal year overlaps with existing year';
    END IF;

    -- Create fiscal year
    INSERT INTO fiscal_years (tenant_id, name, start_month, start_date, end_date)
    VALUES (p_tenant_id, p_name, p_start_month, v_start_date, v_end_date)
    RETURNING id INTO v_fiscal_year_id;

    -- Create 12 monthly periods
    FOR i IN 0..11 LOOP
        v_period_start := (v_start_date + (i || ' months')::INTERVAL)::DATE;
        v_period_end := ((v_start_date + ((i + 1) || ' months')::INTERVAL) - INTERVAL '1 day')::DATE;
        v_period_name := TO_CHAR(v_period_start, 'YYYY-MM');

        INSERT INTO fiscal_periods (
            tenant_id, fiscal_year_id, period_number,
            period_name, start_date, end_date, status
        )
        VALUES (
            p_tenant_id,
            v_fiscal_year_id,
            i + 1,
            v_period_name,
            v_period_start,
            v_period_end,
            'OPEN'
        );
    END LOOP;

    RETURN v_fiscal_year_id;
END;
$$ LANGUAGE plpgsql;
