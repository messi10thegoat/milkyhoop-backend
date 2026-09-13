BEGIN;
DROP TRIGGER trg_credit_note_kaitan_faktur_beku ON credit_notes;
DROP FUNCTION credit_note_kaitan_faktur_beku();
DELETE FROM schema_migrations WHERE version = 'V250__pagar_kaitan_faktur_nota_kredit.sql';
COMMIT;
