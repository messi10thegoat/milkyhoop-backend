-- V276__realtime_g4.sql — Realtime G4: Kas & Bank (bank_transactions + bank_transfers).
-- Same generic mechanism: table-agnostic notify_doc_changed() via DO-loop array.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['bank_transactions', 'bank_transfers'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_notify_doc_changed ON %I', t);
        EXECUTE format(
            'CREATE TRIGGER trg_notify_doc_changed AFTER INSERT OR UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION notify_doc_changed()', t);
    END LOOP;
END $$;
