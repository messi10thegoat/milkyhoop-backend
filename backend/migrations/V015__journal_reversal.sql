-- Migration: V015__journal_reversal.sql
-- Purpose: Add first-class reversal support to journal entries
-- Date: 2026-01-04

-- ============================================
-- 1. Add reversal tracking columns
-- ============================================
ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS reversal_of_id UUID;

ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS reversed_by_id UUID;

ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS reversal_reason TEXT;

ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS reversed_at TIMESTAMPTZ;

-- ============================================
-- 2. Index for reversal lookups
-- ============================================
CREATE INDEX IF NOT EXISTS idx_je_reversal_of
    ON journal_entries(reversal_of_id)
    WHERE reversal_of_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_je_reversed_by
    ON journal_entries(reversed_by_id)
    WHERE reversed_by_id IS NOT NULL;

-- ============================================
-- 3. Constraint: A journal can only be reversed once
-- ============================================
-- This ensures 1 journal → max 1 reversal
CREATE UNIQUE INDEX IF NOT EXISTS idx_je_single_reversal
    ON journal_entries(reversal_of_id)
    WHERE reversal_of_id IS NOT NULL;

-- ============================================
-- 4. Helper function: Check if journal is reversed
-- ============================================
CREATE OR REPLACE FUNCTION is_journal_reversed(
    p_journal_id UUID
)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM journal_entries
        WHERE reversal_of_id = p_journal_id
        LIMIT 1
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- 5. Helper function: Get reversal journal
-- ============================================
CREATE OR REPLACE FUNCTION get_reversal_journal(
    p_journal_id UUID
)
RETURNS TABLE (
    id UUID,
    journal_number TEXT,
    journal_date DATE,
    reversal_reason TEXT,
    reversed_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        je.id,
        je.journal_number,
        je.journal_date,
        je.reversal_reason,
        je.reversed_at
    FROM journal_entries je
    WHERE je.reversal_of_id = p_journal_id
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- Done
-- ============================================
COMMENT ON COLUMN journal_entries.reversal_of_id IS
    'UUID of the journal this entry reverses. NULL if not a reversal.';
COMMENT ON COLUMN journal_entries.reversed_by_id IS
    'UUID of the reversal journal that reversed this entry. NULL if not reversed.';
COMMENT ON COLUMN journal_entries.reversal_reason IS
    'Mandatory reason when creating a reversal.';
COMMENT ON COLUMN journal_entries.reversed_at IS
    'Timestamp when this journal was marked as reversed.';
