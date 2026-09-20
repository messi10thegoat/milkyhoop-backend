-- V275__realtime_g3.sql — Realtime G3.
-- Generic mechanism: notify_doc_changed() is table-agnostic (TG_TABLE_NAME in payload).
-- Attach it to the G3 header tables via a DO-loop over an array so future slices add
-- ONE array line rather than bespoke DDL. pg_notify is transactional (rollback = 0 event).

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['receive_payments', 'customer_deposits'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_notify_doc_changed ON %I', t);
        EXECUTE format(
            'CREATE TRIGGER trg_notify_doc_changed AFTER INSERT OR UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION notify_doc_changed()', t);
    END LOOP;
END $$;

-- Applications: an allocation/application change alters BOTH its parent (payment/
-- deposit/credit-note) AND the invoice outstanding. Deposit & receive-payment apply
-- also UPDATE sales_invoices (so the invoice event fires via that trigger), but a
-- CREDIT-NOTE application does NOT touch sales_invoices -> the invoice event would be
-- missed. This trigger emits BOTH explicitly (duplicate invoice events coalesce ~150ms).
-- An unmapped parent tbl (credit_notes until G5) is dropped fail-closed by the hub.
CREATE OR REPLACE FUNCTION notify_application_changed()
RETURNS trigger AS $$
DECLARE
    r jsonb;
    v_tenant text;
    v_parent_id text;
    v_invoice_id text;
    v_parent_tbl text := TG_ARGV[0];
    v_parent_col text := TG_ARGV[1];
BEGIN
    IF TG_OP = 'DELETE' THEN r := to_jsonb(OLD); ELSE r := to_jsonb(NEW); END IF;
    v_tenant     := r->>'tenant_id';
    v_parent_id  := r->>v_parent_col;
    v_invoice_id := r->>'invoice_id';
    IF v_parent_id IS NOT NULL THEN
        PERFORM pg_notify('doc_changed', json_build_object(
            'tenant_id', v_tenant, 'tbl', v_parent_tbl, 'id', v_parent_id, 'op', TG_OP)::text);
    END IF;
    IF v_invoice_id IS NOT NULL THEN
        PERFORM pg_notify('doc_changed', json_build_object(
            'tenant_id', v_tenant, 'tbl', 'sales_invoices', 'id', v_invoice_id, 'op', 'UPDATE')::text);
    END IF;
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_application_changed ON receive_payment_allocations;
CREATE TRIGGER trg_notify_application_changed
    AFTER INSERT OR UPDATE OR DELETE ON receive_payment_allocations
    FOR EACH ROW EXECUTE FUNCTION notify_application_changed('receive_payments', 'payment_id');

DROP TRIGGER IF EXISTS trg_notify_application_changed ON customer_deposit_applications;
CREATE TRIGGER trg_notify_application_changed
    AFTER INSERT OR UPDATE OR DELETE ON customer_deposit_applications
    FOR EACH ROW EXECUTE FUNCTION notify_application_changed('customer_deposits', 'deposit_id');

DROP TRIGGER IF EXISTS trg_notify_application_changed ON credit_note_applications;
CREATE TRIGGER trg_notify_application_changed
    AFTER INSERT OR UPDATE OR DELETE ON credit_note_applications
    FOR EACH ROW EXECUTE FUNCTION notify_application_changed('credit_notes', 'credit_note_id');
