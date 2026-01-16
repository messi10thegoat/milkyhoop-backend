-- ============================================
-- V029: Journal Immutability Triggers
-- ============================================
-- Enforce audit-grade immutability at database level.
-- POSTED journal entries cannot be modified or deleted.
-- Only allowed operations:
--   1. POSTED → VOID status change (via void_journal)
--   2. Setting reversed_by_id/reversed_at (via reverse_journal)
--
-- This is a CRITICAL audit compliance requirement.
-- ============================================

-- ============================================
-- 1. Prevent UPDATE on POSTED journal_entries
-- ============================================
CREATE OR REPLACE FUNCTION prevent_posted_journal_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Only apply checks if original status was POSTED
    IF OLD.status = 'POSTED' THEN
        -- Allow legitimate void flow: POSTED → VOID
        IF NEW.status = 'VOID' AND OLD.status = 'POSTED' THEN
            RETURN NEW;
        END IF;

        -- Allow reversed_by_id and reversed_at updates (for reversal linking)
        IF (NEW.reversed_by_id IS DISTINCT FROM OLD.reversed_by_id
            OR NEW.reversed_at IS DISTINCT FROM OLD.reversed_at) THEN
            -- Only allow if other critical fields unchanged
            IF NEW.total_debit = OLD.total_debit
               AND NEW.total_credit = OLD.total_credit
               AND NEW.journal_date = OLD.journal_date
               AND NEW.description = OLD.description
               AND NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
        END IF;

        -- Allow voided_by and void_reason updates (during void process)
        IF (NEW.voided_by IS DISTINCT FROM OLD.voided_by
            OR NEW.void_reason IS DISTINCT FROM OLD.void_reason)
           AND NEW.status = 'VOID' THEN
            RETURN NEW;
        END IF;

        -- Block all other modifications to POSTED journals
        IF NEW.total_debit != OLD.total_debit
           OR NEW.total_credit != OLD.total_credit
           OR NEW.journal_date != OLD.journal_date
           OR NEW.description != OLD.description THEN
            RAISE EXCEPTION 'Cannot modify POSTED journal entry (%). Use reversal instead.', OLD.journal_number;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

-- Drop existing trigger if any
DROP TRIGGER IF EXISTS trg_prevent_posted_journal_update ON journal_entries;

-- Create trigger
CREATE TRIGGER trg_prevent_posted_journal_update
    BEFORE UPDATE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_update();

-- ============================================
-- 2. Prevent DELETE on POSTED journal_entries
-- ============================================
CREATE OR REPLACE FUNCTION prevent_posted_journal_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('POSTED', 'VOID') THEN
        RAISE EXCEPTION 'Cannot delete % journal entry (%). Financial history must be preserved.',
            OLD.status, OLD.journal_number;
    END IF;
    RETURN OLD;
END;
$$;

-- Drop existing trigger if any
DROP TRIGGER IF EXISTS trg_prevent_posted_journal_delete ON journal_entries;

-- Create trigger
CREATE TRIGGER trg_prevent_posted_journal_delete
    BEFORE DELETE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_delete();

-- ============================================
-- 3. Prevent modification of journal_lines for POSTED entries
-- ============================================
CREATE OR REPLACE FUNCTION prevent_posted_journal_line_modification()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_journal_status TEXT;
    v_journal_number TEXT;
BEGIN
    -- Get journal status
    IF TG_OP = 'DELETE' THEN
        SELECT status, journal_number INTO v_journal_status, v_journal_number
        FROM journal_entries WHERE id = OLD.journal_id;
    ELSE
        SELECT status, journal_number INTO v_journal_status, v_journal_number
        FROM journal_entries WHERE id = NEW.journal_id;
    END IF;

    -- Block modifications if journal is POSTED or VOID
    IF v_journal_status IN ('POSTED', 'VOID') THEN
        RAISE EXCEPTION 'Cannot modify lines of % journal entry (%). Use reversal instead.',
            v_journal_status, v_journal_number;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$$;

-- Drop existing trigger if any
DROP TRIGGER IF EXISTS trg_prevent_posted_journal_line_modification ON journal_lines;

-- Create trigger
CREATE TRIGGER trg_prevent_posted_journal_line_modification
    BEFORE UPDATE OR DELETE ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION prevent_posted_journal_line_modification();

-- ============================================
-- Comments for documentation
-- ============================================
COMMENT ON FUNCTION prevent_posted_journal_update() IS
    'Prevents modification of POSTED journal entries except for void/reversal flows.';

COMMENT ON FUNCTION prevent_posted_journal_delete() IS
    'Prevents deletion of POSTED or VOID journal entries.';

COMMENT ON FUNCTION prevent_posted_journal_line_modification() IS
    'Prevents modification or deletion of journal lines belonging to POSTED/VOID entries.';

-- ============================================
-- Done
-- ============================================
