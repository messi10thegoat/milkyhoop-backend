-- V186: tenant-level default quote text (opening/closing) on accounting_settings.
--
-- Additive, nullable columns backing the Sales Quote text/bank feature: a tenant
-- can store default opening/closing paragraphs that pre-fill new quotes. Safe to
-- apply online; no backfill required (NULL = no default, FE falls back to empty).

ALTER TABLE accounting_settings ADD COLUMN IF NOT EXISTS default_quote_opening_text text;
ALTER TABLE accounting_settings ADD COLUMN IF NOT EXISTS default_quote_closing_text text;
