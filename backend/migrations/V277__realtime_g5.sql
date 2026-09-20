-- V277__realtime_g5.sql — Realtime G5: credit_notes. Generic notify_doc_changed via array.
-- Also lights up the credit_notes PARENT event from notify_application_changed (G3) which
-- was emitted but dropped fail-closed until now.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['credit_notes'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_notify_doc_changed ON %I', t);
        EXECUTE format(
            'CREATE TRIGGER trg_notify_doc_changed AFTER INSERT OR UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION notify_doc_changed()', t);
    END LOOP;
END $$;
