-- =============================================================================
-- V176 — Self-healing get_next_journal_number() + sequence backfill/resync
-- =============================================================================
-- Purpose (Phase A of journal-number proper-fix):
--   The journal-number counter (journal_number_sequences) and the actual
--   emitted numbers (journal_entries.journal_number, uq_je_tenant_number) are
--   two sources of truth. A maintenance op created JV-numbered journals WITHOUT
--   bumping the counter -> counter drifted BEHIND actual -> next post computed
--   an already-used number -> unique violation -> txn rollback -> the counter
--   increment rolled back too -> deterministic deadlock.
--
--   This migration makes the canonical generator SELF-HEALING: it reconciles
--   the counter against the actual max emitted suffix on every call (GREATEST),
--   so a drifted sequence repairs itself instead of looping on a collision.
--   It also one-time backfills (resyncs) every existing sequence row.
--
-- Backward-compat:
--   - Adds an optional 3rd arg p_date (DEFAULT CURRENT_DATE). Existing 2-arg
--     callers get_next_journal_number(tid, prefix) keep working unchanged -
--     they now resolve to the new self-healing body via the p_date default.
--   - IMPORTANT: adding a parameter does NOT replace the old 2-arg overload
--     (CREATE OR REPLACE only matches an identical signature). We therefore
--     explicitly DROP the stale 2-arg overload so every 2-arg caller dispatches
--     to the self-healing 3-arg version instead of the old non-healing one.
--   - Healthy sequences (counter == max) yield GREATEST(counter+1, max+1) =
--     counter+1 -> identical behavior to the old fn.
--   - Drifted sequences self-heal to max+1.
--
-- Concurrency:
--   - The INSERT ... ON CONFLICT DO UPDATE takes a row lock on the sequence row,
--     serializing concurrent generators for the same (tenant,prefix,year,month).
--   - GREATEST(counter+1, v_actual_max+1) is robust even if a concurrent reader
--     observed a stale v_actual_max (the counter side still advances monotonically).
--
-- Anchored regex (CRITICAL):
--   - The actual-max scan matches '^<prefix>-<yymm>-[0-9]+$' so prefix 'JV' does
--     NOT capture sibling prefixes like 'JV-LB' / 'JV-RECON'.
--
-- Idempotency:
--   - CREATE OR REPLACE FUNCTION + backfill UPDATE that can only INCREASE
--     last_number (GREATEST). Re-running is a no-op once converged.
--
-- Iron Laws:
--   - No financial amounts. No CoA mutation. No UUID literals. Pure number-gen.
-- =============================================================================

-- ----------------------------------------------------------------------------
-- 1. Self-healing canonical generator
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_next_journal_number(
    p_tenant_id text,
    p_prefix    text DEFAULT 'JV',
    p_date      date DEFAULT CURRENT_DATE
)
RETURNS text
LANGUAGE plpgsql
AS $function$
DECLARE
    v_year         INTEGER := EXTRACT(YEAR  FROM p_date)::INTEGER;
    v_month        INTEGER := EXTRACT(MONTH FROM p_date)::INTEGER;
    v_yymm         TEXT    := to_char(p_date, 'YYMM');
    v_actual_max   INTEGER;
    v_number       INTEGER;
BEGIN
    -- Highest numeric suffix already emitted for this tenant/prefix/yymm.
    -- Anchored pattern so 'JV' does not match 'JV-LB-...' etc.
    SELECT COALESCE(MAX((regexp_match(journal_number, '^' || p_prefix || '-' || v_yymm || '-([0-9]+)$'))[1]::int), 0)
      INTO v_actual_max
      FROM journal_entries
     WHERE tenant_id = p_tenant_id
       AND journal_number ~ ('^' || p_prefix || '-' || v_yymm || '-[0-9]+$');

    -- Upsert + self-heal: counter advances monotonically AND never falls behind
    -- the actual emitted max.
    INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
    VALUES (p_tenant_id, p_prefix, v_year, v_month, GREATEST(1, v_actual_max + 1))
    ON CONFLICT (tenant_id, prefix, year, month)
    DO UPDATE SET
        last_number = GREATEST(journal_number_sequences.last_number + 1, v_actual_max + 1),
        updated_at  = NOW()
    RETURNING last_number INTO v_number;

    RETURN p_prefix || '-' || v_yymm || '-' || lpad(v_number::TEXT, 4, '0');
END;
$function$;

-- ----------------------------------------------------------------------------
-- 1b. Drop the stale 2-arg overload (non-self-healing, CURRENT_DATE-only).
--     Adding p_date created a NEW overload rather than replacing the old one;
--     a 2-arg call would otherwise still bind to the old non-healing body.
--     Dropping it makes get_next_journal_number(tid, prefix) dispatch to the
--     self-healing 3-arg version via its p_date DEFAULT CURRENT_DATE.
--     IF EXISTS -> idempotent (re-run after drop is a no-op).
-- ----------------------------------------------------------------------------
DROP FUNCTION IF EXISTS get_next_journal_number(text, text);

-- ----------------------------------------------------------------------------
-- 2. One-time backfill / resync of EVERY existing sequence row.
--    Only increases last_number (GREATEST) -> safe + idempotent.
--    Anchored actual-max derived per (tenant, prefix, year, month).
-- ----------------------------------------------------------------------------
UPDATE journal_number_sequences s
   SET last_number = GREATEST(s.last_number, m.actual_max),
       updated_at  = NOW()
  FROM (
        SELECT s2.tenant_id,
               s2.prefix,
               s2.year,
               s2.month,
               COALESCE(MAX((regexp_match(je.journal_number,
                    '^' || s2.prefix || '-' ||
                    to_char(make_date(s2.year, s2.month, 1), 'YYMM') ||
                    '-([0-9]+)$'))[1]::int), 0) AS actual_max
          FROM journal_number_sequences s2
          LEFT JOIN journal_entries je
                 ON je.tenant_id = s2.tenant_id
                AND je.journal_number ~ ('^' || s2.prefix || '-' ||
                    to_char(make_date(s2.year, s2.month, 1), 'YYMM') || '-[0-9]+$')
         GROUP BY s2.tenant_id, s2.prefix, s2.year, s2.month
       ) m
 WHERE s.tenant_id = m.tenant_id
   AND s.prefix    = m.prefix
   AND s.year      = m.year
   AND s.month     = m.month
   AND s.last_number < m.actual_max;

-- ----------------------------------------------------------------------------
-- 3. Verify-gate: invariants must hold or the migration aborts.
--    (a) 0 sequence rows are behind their anchored actual emitted max.
--    (b) the 3-arg signature exists.
-- ----------------------------------------------------------------------------
DO $verify$
DECLARE
    v_drifted INTEGER;
    v_sig     INTEGER;
BEGIN
    SELECT COUNT(*)
      INTO v_drifted
      FROM journal_number_sequences s
      JOIN LATERAL (
            SELECT COALESCE(MAX((regexp_match(je.journal_number,
                     '^' || s.prefix || '-' ||
                     to_char(make_date(s.year, s.month, 1), 'YYMM') ||
                     '-([0-9]+)$'))[1]::int), 0) AS actual_max
              FROM journal_entries je
             WHERE je.tenant_id = s.tenant_id
               AND je.journal_number ~ ('^' || s.prefix || '-' ||
                   to_char(make_date(s.year, s.month, 1), 'YYMM') || '-[0-9]+$')
           ) m ON TRUE
     WHERE s.last_number < m.actual_max;

    IF v_drifted <> 0 THEN
        RAISE EXCEPTION 'V176 verify-gate FAILED: % sequence row(s) still behind actual max after backfill', v_drifted;
    END IF;

    -- Exactly ONE overload must remain, and it must be the 3-arg signature.
    -- (The stale 2-arg overload must have been dropped so 2-arg callers bind here.)
    SELECT COUNT(*)
      INTO v_sig
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.proname = 'get_next_journal_number'
       AND n.nspname = 'public';

    IF v_sig <> 1 THEN
        RAISE EXCEPTION 'V176 verify-gate FAILED: expected exactly 1 get_next_journal_number overload, found % (stale 2-arg not dropped?)', v_sig;
    END IF;

    SELECT COUNT(*)
      INTO v_sig
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.proname = 'get_next_journal_number'
       AND n.nspname = 'public'
       AND pg_get_function_identity_arguments(p.oid) = 'p_tenant_id text, p_prefix text, p_date date';

    IF v_sig <> 1 THEN
        RAISE EXCEPTION 'V176 verify-gate FAILED: surviving overload is not get_next_journal_number(text,text,date)';
    END IF;

    RAISE NOTICE 'V176 verify-gate PASSED: 0 drifted sequences; single 3-arg self-healing overload present.';
END
$verify$;
