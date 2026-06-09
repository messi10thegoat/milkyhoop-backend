-- BUG-1C / Surprise #28 followup (Step 3):
-- Add idempotency_key infrastructure to production_completions.
--
-- Context: BUG-1C-3 surfaced 2 orphan PRODUCTION_OUTPUT journals on WO-31
-- grapgrap with no surviving companion completion row (post-hoc DELETE
-- suspected, no row-level audit available to confirm). Surprise #28 found
-- that the report_output advisory lock was placed AFTER the completion
-- INSERT, allowing concurrent retries (e.g. allow_overrun=true) to both
-- pass the over-output guard with identical existing_completed snapshot
-- and both proceed to INSERT + emit journal.
--
-- Step 2 fix: lock hoisted to top of tx (code: production.py report_output).
-- Step 3 (this migration): add idempotency_key column + unique partial
-- index so the handler can later ON CONFLICT DO NOTHING on duplicate
-- retries. Handler wiring deferred to next session (frontend must wire
-- key generation per submission).
--
-- The unique index is PARTIAL (WHERE idempotency_key IS NOT NULL) so that
-- legacy rows (NULL key) are not affected — backward compatible.

BEGIN;

ALTER TABLE production_completions
  ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS production_completions_idempotency_uq
  ON production_completions (production_order_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

-- Verify-gate (V168 Surprise #20 pattern): fail-loud if any duplicate
-- (production_order_id, idempotency_key) already exists where key IS NOT
-- NULL. Should be 0 on first apply since column is new and all values
-- are NULL.
DO $$
DECLARE
  dup_count INT;
BEGIN
  SELECT COUNT(*) INTO dup_count FROM (
    SELECT production_order_id, idempotency_key, COUNT(*) AS c
    FROM production_completions
    WHERE idempotency_key IS NOT NULL
    GROUP BY 1, 2
    HAVING COUNT(*) > 1
  ) dups;
  IF dup_count > 0 THEN
    RAISE EXCEPTION
      'V171 verify-gate FAILED: production_completions has % duplicate (production_order_id, idempotency_key) groups',
      dup_count;
  END IF;
  RAISE NOTICE 'V171 verify-gate OK: 0 duplicate idempotency_key groups';
END $$;

COMMIT;
