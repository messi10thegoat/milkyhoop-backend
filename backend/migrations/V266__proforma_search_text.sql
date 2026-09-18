-- V266: Powerful Search coverage for proformas (search_text + trigger + GIN).
-- Metadata-only. pg_trgm+unaccent already installed (V265).
BEGIN;
ALTER TABLE proformas ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_proformas() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.proforma_number, NEW.customer_name, NEW.purpose, NEW.terms, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_proformas ON proformas;
CREATE TRIGGER trg_search_text_proformas BEFORE INSERT OR UPDATE ON proformas
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_proformas();
UPDATE proformas SET search_text = lower(unaccent(concat_ws(' ', proforma_number, customer_name, purpose, terms, notes)));
CREATE INDEX IF NOT EXISTS idx_proformas_search_trgm ON proformas USING gin (search_text gin_trgm_ops);
COMMIT;
