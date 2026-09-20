-- V279__realtime_g7_wholeapp.sql — Realtime G7: whole-app remainder (24 header tables).
-- Generic table-agnostic notify_doc_changed via DO-loop array (23 tables that have an
-- `id` column). tenant_config is keyed by tenant_id (NO id) so it gets a dedicated
-- trigger that emits id = tenant_id. journal_entries is the only high-churn header
-- (1 NOTIFY/posting) -- coalesce (150ms) + bulk_changed (>20) absorb it. Children,
-- ledger (journal_lines/inventory_ledger), logs, and AR/AP caches are excluded.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'products','customers','vendors','chart_of_accounts','bank_accounts','warehouses',
        'product_units','tax_codes','fiscal_periods','journal_entries','expenses',
        'vendor_credits','vendor_deposits','stock_adjustments','employees','salary_components',
        'pay_groups','payroll_runs','production_orders','work_centers','bill_of_materials',
        'user_tenant_roles','payment_requests'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_notify_doc_changed ON %I', t);
        EXECUTE format(
            'CREATE TRIGGER trg_notify_doc_changed AFTER INSERT OR UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION notify_doc_changed()', t);
    END LOOP;
END $$;

-- tenant_config: 1 row per tenant, no `id` column -> emit id = tenant_id.
CREATE OR REPLACE FUNCTION notify_settings_changed()
RETURNS trigger AS $$
DECLARE v_tenant text;
BEGIN
    IF TG_OP = 'DELETE' THEN v_tenant := OLD.tenant_id; ELSE v_tenant := NEW.tenant_id; END IF;
    PERFORM pg_notify('doc_changed', json_build_object(
        'tenant_id', v_tenant, 'tbl', 'tenant_config', 'id', v_tenant, 'op', TG_OP)::text);
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_settings_changed ON tenant_config;
CREATE TRIGGER trg_notify_settings_changed
    AFTER INSERT OR UPDATE OR DELETE ON tenant_config
    FOR EACH ROW EXECUTE FUNCTION notify_settings_changed();
