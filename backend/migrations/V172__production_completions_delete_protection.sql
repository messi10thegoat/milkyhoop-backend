-- V172__production_completions_delete_protection.sql
-- Guard: prevent DELETE production_completions when linked POSTED journal exists & not reversed
-- Mechanism class A (post-deletion manual cleanup) close per forensic audit.
-- Symmetric dengan trg_prevent_posted_journal_delete.

BEGIN;

CREATE OR REPLACE FUNCTION trg_prevent_completion_delete_with_posted_journal()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.journal_id IS NOT NULL THEN
    IF EXISTS (
      SELECT 1 FROM journal_entries
      WHERE id = OLD.journal_id
        AND status = 'POSTED'
        AND reversed_by_id IS NULL
    ) THEN
      RAISE EXCEPTION 'Cannot delete production_completion %: linked POSTED journal % not reversed. Use proper void flow.',
        OLD.id, OLD.journal_id;
    END IF;
  END IF;
  RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_prevent_completion_delete_with_posted_journal ON production_completions;

CREATE TRIGGER trg_prevent_completion_delete_with_posted_journal
  BEFORE DELETE ON production_completions
  FOR EACH ROW EXECUTE FUNCTION trg_prevent_completion_delete_with_posted_journal();

-- Verify-gate DO block (V168 pattern)
DO $$
DECLARE
  trg_count INT;
BEGIN
  SELECT COUNT(*) INTO trg_count FROM information_schema.triggers
    WHERE trigger_name = 'trg_prevent_completion_delete_with_posted_journal'
      AND event_object_table = 'production_completions';
  IF trg_count <> 1 THEN
    RAISE EXCEPTION 'V172 verify-gate failed: expected 1 trigger, got %', trg_count;
  END IF;
END $$;

COMMIT;
