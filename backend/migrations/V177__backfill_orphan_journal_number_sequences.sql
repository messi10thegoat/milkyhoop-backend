-- =============================================================================
-- V177 — Backfill explicit sequence rows for orphan journal-number prefix groups
-- =============================================================================
-- Purpose (Phase B of journal-number proper-fix, complement to V176):
--   V176 made get_next_journal_number() self-healing, so a missing sequence row
--   repairs itself on the next call. But several prefixes were historically
--   emitted by inline blocks that bumped a PARENT counter (e.g. JV-VAR / JV-LB /
--   JV-OH / JV-RECON bumped 'JV'; REV-PJ / COGS-VC / TRF-TRF / SA-SA /
--   JV-EXP-EXP / RECLASS-CN-GAP existed with no own counter row at all).
--   Those series are "orphans": journal_entries rows exist with the prefix, but
--   journal_number_sequences has no matching (tenant, prefix, year, month) row.
--
--   This migration makes every such orphan series EXPLICIT by inserting a
--   sequence row anchored to the series' actual emitted max suffix. After this,
--   each visible prefix owns its counter (no parent over-consumption, no dormant
--   landmine). The code-side fix (Phase B) routes each emitted prefix through
--   get_next_journal_number(<that prefix>) so it bumps its OWN row.
--
-- Method (generic, NOT a hardcoded prefix list):
--   - Derive (tenant, anchored-prefix, year, month, actual_max) from
--     journal_entries by matching the canonical shape '<prefix>-YYMM-NNNN', where
--     <prefix> is everything before the final two dash-delimited numeric groups.
--     This mirrors V176's anchored regex semantics, so 'JV-VAR' is captured as
--     its own prefix (NOT folded into 'JV').
--   - LEFT JOIN journal_number_sequences; INSERT the rows that are missing with
--     last_number = actual_max (so the NEXT get_next call returns actual_max+1).
--
-- Idempotency:
--   - INSERT ... ON CONFLICT (tenant_id, prefix, year, month) DO UPDATE SET
--     last_number = GREATEST(existing, actual_max) -> re-running only ever raises
--     last_number to (or leaves it at) the actual max; converges to a no-op.
--
-- Iron Laws:
--   - No financial amounts. No CoA mutation. No UUID literals. Pure number-gen.
-- =============================================================================

WITH parsed AS (
    SELECT
        je.tenant_id,
        -- prefix = everything before the trailing '-YYMM-NNNN'
        (regexp_match(je.journal_number, '^(.+)-[0-9]{4}-[0-9]+$'))[1]               AS prefix,
        -- year/month decoded from the YYMM segment (20YY)
        2000 + substr((regexp_match(je.journal_number, '^.+-([0-9]{4})-[0-9]+$'))[1], 1, 2)::int AS yr,
               substr((regexp_match(je.journal_number, '^.+-([0-9]{4})-[0-9]+$'))[1], 3, 2)::int  AS mo,
        (regexp_match(je.journal_number, '^.+-[0-9]{4}-([0-9]+)$'))[1]::int           AS seq
    FROM journal_entries je
    WHERE je.journal_number ~ '^.+-[0-9]{4}-[0-9]+$'
),
grp AS (
    SELECT tenant_id, prefix, yr AS year, mo AS month, MAX(seq) AS actual_max
    FROM parsed
    -- guard against a nonsense month from a malformed YYMM
    WHERE mo BETWEEN 1 AND 12
    GROUP BY tenant_id, prefix, yr, mo
)
INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number, updated_at)
SELECT g.tenant_id, g.prefix, g.year, g.month, g.actual_max, NOW()
FROM grp g
ON CONFLICT (tenant_id, prefix, year, month)
DO UPDATE SET
    last_number = GREATEST(journal_number_sequences.last_number, EXCLUDED.last_number),
    updated_at  = NOW();

-- ----------------------------------------------------------------------------
-- Verify-gate: 0 orphan prefix groups must remain (every canonical-shape series
-- has a sequence row whose last_number >= its actual emitted max).
-- ----------------------------------------------------------------------------
DO $verify$
DECLARE
    v_orphans INTEGER;
BEGIN
    WITH parsed AS (
        SELECT
            je.tenant_id,
            (regexp_match(je.journal_number, '^(.+)-[0-9]{4}-[0-9]+$'))[1] AS prefix,
            2000 + substr((regexp_match(je.journal_number, '^.+-([0-9]{4})-[0-9]+$'))[1], 1, 2)::int AS yr,
                   substr((regexp_match(je.journal_number, '^.+-([0-9]{4})-[0-9]+$'))[1], 3, 2)::int  AS mo,
            (regexp_match(je.journal_number, '^.+-[0-9]{4}-([0-9]+)$'))[1]::int AS seq
        FROM journal_entries je
        WHERE je.journal_number ~ '^.+-[0-9]{4}-[0-9]+$'
    ),
    grp AS (
        SELECT tenant_id, prefix, yr AS year, mo AS month, MAX(seq) AS actual_max
        FROM parsed
        WHERE mo BETWEEN 1 AND 12
        GROUP BY tenant_id, prefix, yr, mo
    )
    SELECT COUNT(*)
      INTO v_orphans
      FROM grp g
      LEFT JOIN journal_number_sequences s
        ON s.tenant_id = g.tenant_id AND s.prefix = g.prefix
       AND s.year = g.year AND s.month = g.month
     WHERE s.tenant_id IS NULL
        OR s.last_number < g.actual_max;

    IF v_orphans <> 0 THEN
        RAISE EXCEPTION 'V177 verify-gate FAILED: % orphan/behind prefix group(s) remain', v_orphans;
    END IF;

    RAISE NOTICE 'V177 verify-gate PASSED: 0 orphan/behind prefix groups; every canonical series has a sequence row >= its actual max.';
END
$verify$;
