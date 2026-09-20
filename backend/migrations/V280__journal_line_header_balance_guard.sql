-- V280: Law 4 hardening — deferred SUM(journal_lines) == header balance guard.
--
-- The header CHECK `je_balanced` only asserts total_debit == total_credit; NOTHING
-- asserts SUM(journal_lines.debit/credit) == journal_entries.total_debit/credit. A
-- POSTED journal could therefore persist lines that do not sum to its header (real
-- instance BP-2608-0001 lost Rp2.500 between header and lines). There are ~94
-- DRAFT->POSTED posting paths and NO central posting kernel, so this invariant is
-- enforced at the one DB chokepoint every path must cross (Law 13 lesson: an
-- invariant expressible as a predicate over rows belongs in the DB, not in 94 code
-- sites that can each forget it).
--
-- DEFERRABLE INITIALLY DEFERRED: the check runs against the FINAL committed state,
-- so the legitimate multi-step sequence (INSERT header DRAFT -> INSERT lines ->
-- UPDATE status POSTED) is never checked mid-flight — only what actually commits.
-- Only POSTED journals are enforced; DRAFT (still being built) and VOID (superseded)
-- are skipped. Tolerance 0.01 (a full-tenant audit at 0.01 shows 0 current
-- violations, so no backfill/cleanup is required before this ships).

CREATE OR REPLACE FUNCTION guard_journal_line_header_balance()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_journal_id uuid;
    v_status     text;
    v_num        text;
    v_th         numeric(18,2);
    v_tc         numeric(18,2);
    v_sh         numeric(18,2);
    v_sc         numeric(18,2);
BEGIN
    -- Resolve the affected journal id regardless of which table fired.
    IF TG_TABLE_NAME = 'journal_lines' THEN
        v_journal_id := COALESCE(NEW.journal_id, OLD.journal_id);
    ELSE
        v_journal_id := COALESCE(NEW.id, OLD.id);
    END IF;
    IF v_journal_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT je.status, je.journal_number, je.total_debit, je.total_credit
      INTO v_status, v_num, v_th, v_tc
      FROM journal_entries je
     WHERE je.id = v_journal_id;

    -- Journal removed within the same transaction, or not POSTED -> nothing to enforce.
    IF NOT FOUND OR v_status IS DISTINCT FROM 'POSTED' THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(SUM(jl.debit), 0), COALESCE(SUM(jl.credit), 0)
      INTO v_sh, v_sc
      FROM journal_lines jl
     WHERE jl.journal_id = v_journal_id;

    IF ABS(v_th - v_sh) > 0.01 OR ABS(v_tc - v_sc) > 0.01 THEN
        RAISE EXCEPTION
          'Law 4 line/header imbalance on journal %: header debit=% credit=% but lines sum to debit=% credit=% (posting rejected to prevent silent ledger corruption)',
          v_num, v_th, v_tc, v_sh, v_sc
          USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

-- Fires on any line change; catches the initial-post case where the header was
-- computed wrong versus the lines (the BP-2608-0001 class).
DROP TRIGGER IF EXISTS trg_journal_line_header_balance_lines ON journal_lines;
CREATE CONSTRAINT TRIGGER trg_journal_line_header_balance_lines
    AFTER INSERT OR UPDATE OR DELETE ON journal_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION guard_journal_line_header_balance();

-- Fires on the DRAFT->POSTED header flip (manual /post path may touch only the
-- header, not the lines, so the lines trigger alone would miss it).
DROP TRIGGER IF EXISTS trg_journal_line_header_balance_header ON journal_entries;
CREATE CONSTRAINT TRIGGER trg_journal_line_header_balance_header
    AFTER INSERT OR UPDATE ON journal_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION guard_journal_line_header_balance();
