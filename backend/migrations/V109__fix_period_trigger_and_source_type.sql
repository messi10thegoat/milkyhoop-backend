-- V109: Fix period lock trigger to block CLOSED periods + add NOT NULL to source_type
--
-- FIX 1: The trigger prevent_closed_period_journal currently only checks
--         is_period_locked() which misses CLOSED status. System entries
--         (CLOSING, OPENING, ADJUSTMENT, OPENING_BALANCE) should still be
--         allowed into CLOSED periods but blocked from LOCKED periods.
--         Regular entries should be blocked from both CLOSED and LOCKED.
--
-- FIX 2: source_type column should never be NULL. Backfill any NULLs
--         as MANUAL, then add NOT NULL constraint.

-- ── FIX 1 ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION prevent_closed_period_journal()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow system-generated entries (reversals, closings, openings) to CLOSED periods
    -- But LOCKED periods block everything
    IF NEW.source_type IN ('CLOSING', 'OPENING', 'ADJUSTMENT', 'OPENING_BALANCE') THEN
        -- Only block LOCKED for system entries
        IF is_period_locked(NEW.tenant_id, NEW.journal_date) THEN
            RAISE EXCEPTION 'Cannot post to LOCKED period. Period must be reopened first.';
        END IF;
    ELSE
        -- Block both CLOSED and LOCKED for manual/regular entries
        IF is_period_closed(NEW.tenant_id, NEW.journal_date) THEN
            RAISE EXCEPTION 'Cannot post to closed or locked period. Period is %.',
                (SELECT status FROM fiscal_periods
                 WHERE tenant_id = NEW.tenant_id
                 AND NEW.journal_date >= start_date
                 AND NEW.journal_date <= end_date
                 LIMIT 1);
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── FIX 2 ──────────────────────────────────────────────────────────────
-- Backfill any NULL source_type values
UPDATE journal_entries SET source_type = 'MANUAL' WHERE source_type IS NULL;

-- Add NOT NULL constraint
ALTER TABLE journal_entries ALTER COLUMN source_type SET NOT NULL;
