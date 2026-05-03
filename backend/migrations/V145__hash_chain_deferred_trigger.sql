-- ============================================================================
-- V145: Hash chain trigger fix — switch to DEFERRABLE INITIALLY DEFERRED
-- ============================================================================
-- Ticket: HASH-CHAIN-PROD-VIOLATION-001
-- Iron Law 20 (audit trail integrity)
-- Applied to prod: 2026-05-02T03:58:52Z
--
-- Root cause: previous trigger fired BEFORE INSERT/UPDATE on journal_entries.
-- Code paths using Pattern A (direct INSERT with status='POSTED') triggered
-- the hash function before the row + journal_lines existed in the table, so
-- compute_journal_hash saw NULL header + NULL lines and produced a bogus hash.
-- Affected 7 entries in tenant grapgrap (source_types: expense, MANUAL,
-- CREDIT_NOTE void, BANK_TRANSACTION, PRODUCTION_OUTPUT_VOID).
--
-- Fix: replace BEFORE trigger with a CONSTRAINT TRIGGER firing AFTER and
-- DEFERRABLE INITIALLY DEFERRED, so it executes at COMMIT time when both
-- row and lines are populated. Works for both Pattern A and Pattern B.
--
-- Recursion guard: NEW.chain_sequence IS NULL — the trigger's own UPDATE
-- writes chain_sequence, so a re-fire on that update skips.
--
-- prevent_posted_journal_update permits the trigger's own UPDATE because it
-- only protects 4 fields (total_debit, total_credit, journal_date,
-- description) — none of the hash columns. Verified empirically.
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.assign_hash_and_sequence()
RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE
    v_prev_hash VARCHAR;
    v_max_seq BIGINT;
    v_new_seq BIGINT;
    v_new_hash VARCHAR;
BEGIN
    IF NEW.status = 'POSTED'
       AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'POSTED')
       AND NEW.chain_sequence IS NULL THEN
        PERFORM pg_advisory_xact_lock(hashtext('journal_chain:' || NEW.tenant_id));
        SELECT COALESCE(MAX(chain_sequence), 0) INTO v_max_seq
          FROM journal_entries
          WHERE tenant_id = NEW.tenant_id AND status = 'POSTED' AND id != NEW.id;
        v_new_seq := v_max_seq + 1;
        SELECT content_hash INTO v_prev_hash
          FROM journal_entries
          WHERE tenant_id = NEW.tenant_id AND chain_sequence = v_max_seq
            AND status = 'POSTED' AND id != NEW.id;
        v_prev_hash := COALESCE(v_prev_hash, 'GENESIS');
        v_new_hash := compute_journal_hash(NEW.id, v_prev_hash);
        UPDATE journal_entries
           SET chain_sequence = v_new_seq, previous_hash = v_prev_hash, content_hash = v_new_hash
         WHERE id = NEW.id;
    END IF;
    RETURN NULL;
END;
$function$;

DROP TRIGGER trg_assign_hash_sequence ON journal_entries;
CREATE CONSTRAINT TRIGGER trg_assign_hash_sequence
    AFTER INSERT OR UPDATE ON journal_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION assign_hash_and_sequence();

COMMIT;
