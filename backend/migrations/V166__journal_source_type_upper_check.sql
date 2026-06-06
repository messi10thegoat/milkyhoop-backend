-- =============================================================================
-- V166 — journal_entries.source_type uppercase CHECK constraint guard
-- =============================================================================
-- Purpose:
--   Defense-in-depth for V160 normalization. Closes Surprise #9 (case
--   convention class) by adding DB-level CHECK constraint that rejects
--   any future lowercase source_type emit.
--
--   Recon (2026-06-06):
--     SELECT source_type, COUNT(*) FROM journal_entries
--      WHERE source_type ~ '[a-z]' GROUP BY source_type;
--     -> 0 rows (V160 normalize complete).
--
--   Additionally adds CHECK guard on journal_source_types lookup table so
--   no new lowercase source_type can be registered (defense in depth).
--
--   Also registers two source_types that emit-path fixes resurfaced:
--     BRANCH_TRANSFER  — routers/branches.py inter-branch journal
--     EXPENSE_REVERSAL — routers/expenses.py void path reversal
--   Without these the post-fix code path fails FK insertion.
--
-- Scope:
--   1. INSERT BRANCH_TRANSFER + EXPENSE_REVERSAL into journal_source_types
--      (ON CONFLICT DO NOTHING). BRANCH_TRANSFER may already exist.
--   2. ADD CHECK constraint chk_journal_source_type_upper on
--      journal_entries.source_type.
--   3. ADD CHECK constraint chk_jst_source_type_upper on
--      journal_source_types.source_type.
--
-- Verification:
--   BEGIN; INSERT lowercase source_type -> expect CHECK violation; ROLLBACK.
--
-- Pre-Fase 6 prep — owner-approved 2026-06-06 (Surprise #9 class closure).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Register newly-fixed source_types in lookup table
-- -----------------------------------------------------------------------------
INSERT INTO journal_source_types (source_type)
VALUES ('BRANCH_TRANSFER'), ('EXPENSE_REVERSAL')
ON CONFLICT (source_type) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 2. Guard journal_entries.source_type — UPPER-only
-- -----------------------------------------------------------------------------
ALTER TABLE journal_entries
    DROP CONSTRAINT IF EXISTS chk_journal_source_type_upper;

ALTER TABLE journal_entries
    ADD CONSTRAINT chk_journal_source_type_upper
    CHECK (source_type = UPPER(source_type));

-- -----------------------------------------------------------------------------
-- 3. Guard journal_source_types.source_type — UPPER-only (lookup integrity)
-- -----------------------------------------------------------------------------
ALTER TABLE journal_source_types
    DROP CONSTRAINT IF EXISTS chk_jst_source_type_upper;

ALTER TABLE journal_source_types
    ADD CONSTRAINT chk_jst_source_type_upper
    CHECK (source_type = UPPER(source_type));

COMMIT;
