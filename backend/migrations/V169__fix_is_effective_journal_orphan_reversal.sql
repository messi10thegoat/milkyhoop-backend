-- Surprise #23 Fix: is_effective_journal() must exclude orphan reversal entries
-- Symmetry bug: target VOID excluded via reversed_by_id, but counter-entry slipped
-- through unless its source_type matched a hardcoded list. Add reversal_of_id IS NULL
-- for source-type-agnostic symmetric handling.

CREATE OR REPLACE FUNCTION is_effective_journal(p_journal_id UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS(
        SELECT 1 FROM journal_entries je
        WHERE je.id = p_journal_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND je.reversal_of_id IS NULL
          AND je.source_type NOT IN (
              'INVOICE_REVERSAL',
              'BILL_REVERSAL',
              'INVOICE_FULFILLMENT_REVERSAL',
              'SALES_INVOICE_COGS_REVERSAL'
          )
    );
$$;

COMMENT ON FUNCTION is_effective_journal(UUID) IS
'Rule 8.1 (v2, Surprise #23 fix 2026-06-08): TRUE for journals counting in AR/AP/GL aggregations. Excludes (a) reversed originals via reversed_by_id, (b) reversal counter-entries via reversal_of_id (symmetric, source-type-agnostic), and (c) legacy reversal source_types as belt-and-suspenders.';

-- Verify-gate: no orphan-effective reversal entries may survive
DO $$
DECLARE
    orphan_count INT;
BEGIN
    SELECT COUNT(*) INTO orphan_count
    FROM journal_entries je
    WHERE je.status = 'POSTED'
      AND je.reversal_of_id IS NOT NULL
      AND is_effective_journal(je.id) = true;
    IF orphan_count > 0 THEN
        RAISE EXCEPTION 'V169 verify-gate FAILED: is_effective_journal still includes % orphan-effective reversal entries', orphan_count;
    END IF;
    RAISE NOTICE 'V169 verify-gate OK: 0 orphan-effective reversal entries';
END $$;
