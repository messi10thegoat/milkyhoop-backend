BEGIN;
DROP TRIGGER trg_prevent_reverse_of_reversal ON journal_entries;
DROP FUNCTION cegah_pembalik_atas_pembalik();
DELETE FROM schema_migrations WHERE version = 'V254__cegah_pembalik_atas_pembalik.sql';
COMMIT;
