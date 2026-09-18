BEGIN;
DROP TRIGGER IF EXISTS trg_search_text_proformas ON proformas;
DROP FUNCTION IF EXISTS fn_search_text_proformas();
DROP INDEX IF EXISTS idx_proformas_search_trgm;
ALTER TABLE proformas DROP COLUMN IF EXISTS search_text;
COMMIT;
