-- V278__realtime_g6.sql — Realtime G6: quotes (Penawaran). Generic notify_doc_changed via array.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['quotes'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_notify_doc_changed ON %I', t);
        EXECUTE format(
            'CREATE TRIGGER trg_notify_doc_changed AFTER INSERT OR UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION notify_doc_changed()', t);
    END LOOP;
END $$;
