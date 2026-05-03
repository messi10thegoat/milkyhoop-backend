-- ============================================================================
-- V145 ROLLBACK — restore BEFORE trigger
-- ============================================================================
-- Ticket: HASH-CHAIN-PROD-VIOLATION-001
--
-- WARNING: this rollback only restores the trigger to its pre-V145 form.
-- It does NOT undo the V145__backfill_grapgrap_walk_rebuild.sql data
-- migration (312 rows of grapgrap chain were rewritten). Data rollback
-- requires restore from snapshot taken before 2026-05-02T03:58:52Z, e.g.
-- restic snapshot from /root/milkyhoop-dev/backups/restic_offsite.sh or
-- the encrypted daily backup at /root/milkyhoop-dev/backups/.
--
-- Reverting the trigger alone reintroduces the original Pattern A bug class.
-- Use only if V145 introduces an unforeseen regression that outweighs the
-- restored bug. In that case, follow up with a corrective design.
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.assign_hash_and_sequence()
RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE
    v_prev_hash VARCHAR;
    v_max_seq BIGINT;
BEGIN
    IF NEW.status = 'POSTED' AND (OLD IS NULL OR OLD.status != 'POSTED') THEN
        PERFORM pg_advisory_xact_lock(hashtext('journal_chain:' || NEW.tenant_id));
        SELECT COALESCE(MAX(chain_sequence), 0) INTO v_max_seq
          FROM journal_entries
          WHERE tenant_id = NEW.tenant_id AND status = 'POSTED';
        NEW.chain_sequence := v_max_seq + 1;
        SELECT content_hash INTO v_prev_hash
          FROM journal_entries
          WHERE tenant_id = NEW.tenant_id
              AND chain_sequence = v_max_seq
              AND status = 'POSTED';
        NEW.previous_hash := COALESCE(v_prev_hash, 'GENESIS');
        NEW.content_hash := compute_journal_hash(NEW.id, NEW.previous_hash);
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER trg_assign_hash_sequence ON journal_entries;
CREATE TRIGGER trg_assign_hash_sequence
    BEFORE INSERT OR UPDATE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION assign_hash_and_sequence();

COMMIT;
