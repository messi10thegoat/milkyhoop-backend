-- V273__guard_single_account_journal.sql
-- E3 (#3): reject a POSTED journal whose ALL legs sit on ONE account.
-- Such a journal is balanced (Law 4 passes) but net-zero on that single account -- no
-- real transaction, only an input error (e.g. the MT-C4057F2C bug: bank tx whose contra
-- account was the bank account itself). Reversals are EXEMPT: a reversal necessarily
-- mirrors whatever it reverses, so a reversal of a pre-existing single-account journal
-- must still be allowed (reversal_of_id IS NOT NULL). DB-level (Iron Law 13): the single
-- chokepoint every posting path crosses.

CREATE OR REPLACE FUNCTION guard_no_single_account_journal()
RETURNS TRIGGER AS $$
DECLARE
    n_lines int;
    n_accts int;
BEGIN
    IF NEW.status = 'POSTED' AND NEW.reversal_of_id IS NULL THEN
        SELECT COUNT(*), COUNT(DISTINCT account_id)
          INTO n_lines, n_accts
          FROM journal_lines WHERE journal_id = NEW.id;
        -- n_lines=0 => lines not attached yet (INSERT-as-POSTED-before-lines path); skip.
        IF n_lines >= 2 AND n_accts = 1 THEN
            RAISE EXCEPTION
                'Jurnal % semua kakinya di SATU akun (net nol, bukan transaksi nyata) -- perlu akun lawan.',
                NEW.journal_number
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_guard_no_single_account_journal ON journal_entries;
CREATE TRIGGER trg_guard_no_single_account_journal
    BEFORE INSERT OR UPDATE ON journal_entries
    FOR EACH ROW
    WHEN (NEW.status = 'POSTED')
    EXECUTE FUNCTION guard_no_single_account_journal();
