-- V219: quotes — add the 5 columns the quote-create feature writes but were never migrated.
--
-- POST /quotes (quotes.py:502) STATICALLY inserts opening_text, closing_text,
-- payment_bank_name, payment_account_number, payment_account_holder (fixed column list,
-- fixed $17-$25 placeholders — not a dynamic builder). None of these columns exist in ANY
-- database (live milkydb, milkydb_saved_20260725, goldenpath_green, prev, contaminated), and
-- `quotes` has 0 rows in all of them -> quote-create has 500'd since the feature was built.
--
-- This is a BUILT-BUT-UNMIGRATED feature, not dead code (FE-oracle proof):
--   - useQuoteForm.ts:594-652 sends all 5 in the POST payload,
--   - prefilled from accounting_settings.default_quote_opening_text/closing_text (added by V186),
--   - QuoteDetail/OverviewTab.tsx reads them back.
-- Per the strip-vs-add rule (FE sends -> add), we add the columns; stripping would delete a
-- fully-built feature and silently swallow user-entered data.
--
-- Column-only backlog scope: these 5 are on the E2E path (step 1, Penawaran). Other drift
-- findings stay in the separate backlog; this migration adds ONLY what step 1 needs.
-- Idempotent (IF NOT EXISTS). No data migration.

ALTER TABLE quotes ADD COLUMN IF NOT EXISTS opening_text            text;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS closing_text            text;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS payment_bank_name       text;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS payment_account_number  text;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS payment_account_holder  text;
