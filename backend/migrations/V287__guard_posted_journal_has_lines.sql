-- V287: refuse any POSTED journal_entries row that has ZERO journal_lines.
--
-- The header-only gap made live once, deliberately: a payroll run whose calculate
-- had failed (0 slip lines) still went Submit->Approve->Post and produced POSTED
-- journal JV-PAY-PAY-2026-09-012 with total_debit/credit 0.00 and ZERO journal_lines,
-- consuming a journal number and a chain_sequence slot. Nothing objected:
--   * je_balanced CHECK passes because 0 - 0 = 0;
--   * V280 guard_journal_line_header_balance passes because SUM(lines)=0 == header 0;
--   * guard_no_single_account_journal DELIBERATELY skips n_lines=0
--     ("lines not attached yet (INSERT-as-POSTED-before-lines path); skip").
-- So a journal that means nothing satisfied every existing guard.
--
-- WHY THE HEADER SIDE, and only the header side: a zero-line journal fires NO
-- per-line trigger (there is no line to fire on), so a journal_lines trigger can
-- never see it. The invariant "a POSTED journal has >= 1 line" is only observable
-- from journal_entries. There are ~94 DRAFT->POSTED paths and no central posting
-- kernel, so it is enforced at the one DB chokepoint (Law 13 lesson).
--
-- DEFERRABLE INITIALLY DEFERRED, mirroring V280: the check runs against the FINAL
-- committed state, so the legitimate sequence (INSERT header DRAFT -> INSERT lines
-- -> UPDATE status POSTED, all one txn) is only ever checked once, at commit, with
-- the lines already present. Measured before shipping (BACKEND, 2026-09-22): the
-- only INSERT-as-POSTED path in the tree (payment_request_service.py) references
-- columns absent from the live schema and cannot persist a journal; every live path
-- attaches its lines in the same transaction as the POSTED flip, proven by V280's
-- own deferred trigger already firing at this exact commit point without breaking
-- them. No carve-out for reversals: a POSTED reversal with zero lines reverses
-- nothing (DB-wide count of such rows today = 0).

CREATE OR REPLACE FUNCTION guard_posted_journal_has_lines()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
    v_num    text;
    n_lines  int;
BEGIN
    SELECT je.status, je.journal_number
      INTO v_status, v_num
      FROM journal_entries je
     WHERE je.id = NEW.id;

    -- Journal removed within the same transaction, or not POSTED -> nothing to enforce.
    IF NOT FOUND OR v_status IS DISTINCT FROM 'POSTED' THEN
        RETURN NULL;
    END IF;

    SELECT COUNT(*) INTO n_lines
      FROM journal_lines jl
     WHERE jl.journal_id = NEW.id;

    IF n_lines = 0 THEN
        RAISE EXCEPTION
          'Jurnal % POSTED tanpa satu pun baris (0 journal_lines): jurnal kosong tak bermakna, posting ditolak.',
          v_num
          USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_posted_journal_has_lines ON journal_entries;
CREATE CONSTRAINT TRIGGER trg_guard_posted_journal_has_lines
    AFTER INSERT OR UPDATE ON journal_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION guard_posted_journal_has_lines();
