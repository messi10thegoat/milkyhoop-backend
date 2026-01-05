-- Migration: V014__period_locking.sql
-- Purpose: Add period locking capabilities to accounting kernel
-- Date: 2026-01-04

-- ============================================
-- 1. Enable btree_gist extension for EXCLUDE constraint
-- ============================================
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ============================================
-- 2. Add lock tracking columns to fiscal_periods
-- ============================================
ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;

ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS locked_by UUID;

ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS lock_reason TEXT;

ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS closing_snapshot JSONB;

ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS closing_journal_id UUID;

ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- ============================================
-- 3. Link journal_entries to fiscal_periods
-- ============================================
ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS period_id UUID REFERENCES fiscal_periods(id);

CREATE INDEX IF NOT EXISTS idx_je_period
    ON journal_entries(period_id);

-- ============================================
-- 4. Constraint: LOCKED periods must be CLOSED first
-- ============================================
-- Drop if exists (for re-runability)
ALTER TABLE fiscal_periods
    DROP CONSTRAINT IF EXISTS chk_locked_must_be_closed;

ALTER TABLE fiscal_periods
    ADD CONSTRAINT chk_locked_must_be_closed
    CHECK (status != 'LOCKED' OR closed_at IS NOT NULL);

-- ============================================
-- 5. CRITICAL: Prevent overlapping periods per tenant
-- This ensures deterministic period resolution
-- ============================================
-- Drop if exists (for re-runability)
ALTER TABLE fiscal_periods
    DROP CONSTRAINT IF EXISTS excl_no_overlap;

ALTER TABLE fiscal_periods
    ADD CONSTRAINT excl_no_overlap
    EXCLUDE USING gist (
        tenant_id WITH =,
        daterange(start_date, end_date, '[]') WITH &&
    );

-- ============================================
-- 6. Helper function: Get period for a specific date
-- ============================================
CREATE OR REPLACE FUNCTION get_fiscal_period_for_date(
    p_tenant_id TEXT,
    p_journal_date DATE
)
RETURNS TABLE (
    id UUID,
    period_name TEXT,
    start_date DATE,
    end_date DATE,
    status TEXT,
    closed_at TIMESTAMPTZ,
    locked_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        fp.id,
        fp.period_name,
        fp.start_date,
        fp.end_date,
        fp.status,
        fp.closed_at,
        fp.locked_at
    FROM fiscal_periods fp
    WHERE fp.tenant_id = p_tenant_id
      AND p_journal_date >= fp.start_date
      AND p_journal_date <= fp.end_date
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- 7. Helper function: Check if date is in locked period
-- ============================================
CREATE OR REPLACE FUNCTION is_period_locked(
    p_tenant_id TEXT,
    p_journal_date DATE
)
RETURNS BOOLEAN AS $$
DECLARE
    v_status TEXT;
BEGIN
    SELECT status INTO v_status
    FROM fiscal_periods
    WHERE tenant_id = p_tenant_id
      AND p_journal_date >= start_date
      AND p_journal_date <= end_date
    LIMIT 1;

    RETURN COALESCE(v_status = 'LOCKED', FALSE);
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- 8. Helper function: Check if date is in closed period
-- ============================================
CREATE OR REPLACE FUNCTION is_period_closed(
    p_tenant_id TEXT,
    p_journal_date DATE
)
RETURNS BOOLEAN AS $$
DECLARE
    v_status TEXT;
BEGIN
    SELECT status INTO v_status
    FROM fiscal_periods
    WHERE tenant_id = p_tenant_id
      AND p_journal_date >= start_date
      AND p_journal_date <= end_date
    LIMIT 1;

    RETURN COALESCE(v_status IN ('CLOSED', 'LOCKED'), FALSE);
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- 9. Index for period status queries
-- ============================================
CREATE INDEX IF NOT EXISTS idx_fiscal_period_status
    ON fiscal_periods(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_fiscal_period_dates
    ON fiscal_periods(tenant_id, start_date, end_date);

-- ============================================
-- Done
-- ============================================
COMMENT ON TABLE fiscal_periods IS
    'Fiscal periods with locking support.
     Status: OPEN (normal), CLOSED (soft close), LOCKED (immutable)';
