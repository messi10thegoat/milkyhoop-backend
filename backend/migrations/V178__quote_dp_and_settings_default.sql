-- FIX_P2_QUOTEDP 2026-06-16
-- P2 Quote Down-Payment (DP) — NO-LEDGER.
-- A Penawaran (Quote) can carry an adjustable down-payment captured as a plain
-- number on the non-posting quote document. Quotes stay ZERO-ledger; the DP
-- field does NOT trigger any journal. The DP receipt + apply happen later (P3).
--
-- Additive only — all columns NULLable, no backfill, no constraints that could
-- break existing rows. Fully idempotent (ADD COLUMN IF NOT EXISTS).
--
--   quotes.dp_amount  numeric(18,2)  CANONICAL (rupiah, source of truth)
--   quotes.dp_percent numeric(5,2)   helper/display
--   accounting_settings.default_dp_percent          numeric(5,2)  tenant default
--   accounting_settings.default_uang_muka_account_id uuid          FK CoA (optional)

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS dp_amount  numeric(18,2);

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS dp_percent numeric(5,2);

ALTER TABLE accounting_settings
    ADD COLUMN IF NOT EXISTS default_dp_percent numeric(5,2);

ALTER TABLE accounting_settings
    ADD COLUMN IF NOT EXISTS default_uang_muka_account_id uuid;

-- Optional FK to chart_of_accounts (deposit liability default). Nullable, no
-- backfill. Guarded so re-apply does not error if it already exists.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_accounting_settings_uang_muka_account'
          AND table_name = 'accounting_settings'
    ) THEN
        ALTER TABLE accounting_settings
            ADD CONSTRAINT fk_accounting_settings_uang_muka_account
            FOREIGN KEY (default_uang_muka_account_id)
            REFERENCES chart_of_accounts(id)
            ON DELETE SET NULL;
    END IF;
END $$;
