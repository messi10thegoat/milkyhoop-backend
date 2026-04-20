-- V139__rule8_invariant_fix.sql
-- Fix Rule 8 invariant: reverse-then-repost phantom AR/AP drift.
-- See milkyhoop-arap v1.4 Rule 8.1.

CREATE OR REPLACE FUNCTION is_effective_journal(p_journal_id UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS(
        SELECT 1 FROM journal_entries je
        WHERE je.id = p_journal_id
          AND je.status = POSTED
          AND je.reversed_by_id IS NULL
          AND je.source_type NOT IN (
              INVOICE_REVERSAL,
              BILL_REVERSAL,
              INVOICE_FULFILLMENT_REVERSAL,
              SALES_INVOICE_COGS_REVERSAL
          )
    );
$$;

COMMENT ON FUNCTION is_effective_journal(UUID) IS
Rule 8.1: TRUE for journals counting in AR/AP aggregations. Excludes reversed originals + reversal journals.;
