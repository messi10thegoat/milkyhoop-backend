-- ============================================================================
-- V215 — recover STANDALONE missing tables (recovery drift, MISSING-TABLE class)
-- ----------------------------------------------------------------------------
-- Same class as V212/V213/V214: code writes/reads them, no migration CREATEs them.
-- ARBITER = CODE. Scope here = tables that are USEFUL ON THEIR OWN (not part of a
-- multi-table feature that needs its siblings to function). The tax/e-faktur set
-- (tax_invoices/_items/_sources, nsfp_assignments, tax_groups/_items, tax_info,
-- product_djp_mapping, efaktur_exports) and expense-claims set are coherent
-- modules handled separately; resto/POS (kds_*, recipe_*, reservations) is
-- irrelevant to a konveksi tenant; notifications (Go notification-service) and
-- policy_audit_log (policy_engine service) are other-service-owned.
--
-- (1) master_data_audit_log — audit trail on master-data edits (vendor/customer/
--     item...). Referenced api_gateway/app/utils/audit_log.py:70 (13-col INSERT,
--     entity_id $3::uuid) + read WHERE tenant_id/entity_type/entity_id, ORDER BY
--     changed_at DESC. Missing → master-data edit audit writes fail.
-- (2) product_units — unit master for items. api_gateway/app/routers/units.py
--     INSERT :186/:640 (tenant_id, name, abbreviation, is_system); SELECT id,
--     name, abbreviation, is_system; ORDER BY is_system DESC, name ASC.
-- (3) journal_sequences — per-tenant counter (fixed_assets.py). INSERT :617
--     (tenant_id, last_number) ON CONFLICT (tenant_id) DO UPDATE last_number+1
--     → PK is tenant_id.
-- (4) tool_call_logs — chat tool-call observability (unified_agent/observability.py,
--     fire-and-forget). INSERT :34 (turn_id, tool_call_id, tool_name,
--     retry_attempt, status, latency_ms, error_type, idempotency_key); UPDATE
--     SET turn_id, fallback_used, idempotency_key; ORDER BY created_at DESC.
--
-- Types: tenant_id TEXT; *_id that the code casts ::uuid or joins on uuid → UUID;
-- uncast text ids (changed_by, turn_id) → TEXT (conservative, no risky cast); id
-- surrogate PK UUID DEFAULT gen_random_uuid() where the INSERT omits id; counts/
-- latency/sequence INTEGER; is_* BOOLEAN; timestamps TIMESTAMPTZ DEFAULT now().
-- No FK / no RLS (matches recovered siblings + V211/V212/V213/V214). Idempotent.
-- ============================================================================

-- (1) ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS master_data_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT        NOT NULL,
    entity_type     TEXT,
    entity_id       UUID,
    entity_name     TEXT,
    action          TEXT,
    field_name      TEXT,
    old_value       TEXT,
    new_value       TEXT,
    changed_by      TEXT,
    changed_by_name TEXT,
    source_ip       TEXT,
    user_agent      TEXT,
    notes           TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mdal_entity
    ON master_data_audit_log (tenant_id, entity_type, entity_id, changed_at DESC);

-- (2) ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_units (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT        NOT NULL,
    name         TEXT,
    abbreviation TEXT,
    is_system    BOOLEAN     NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_product_units_tenant ON product_units (tenant_id);

-- (3) ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS journal_sequences (
    tenant_id   TEXT    PRIMARY KEY,
    last_number INTEGER NOT NULL DEFAULT 0
);

-- (4) ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_call_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id         TEXT,
    tool_call_id    TEXT,
    tool_name       TEXT,
    retry_attempt   INTEGER,
    status          TEXT,
    latency_ms      INTEGER,
    error_type      TEXT,
    idempotency_key TEXT,
    fallback_used   BOOLEAN,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tool_call_logs_created ON tool_call_logs (created_at DESC);

-- Verify column contracts the code needs.
DO $$
DECLARE v_missing TEXT;
BEGIN
    FOR v_missing IN
        SELECT unnest(ARRAY['tenant_id','entity_type','entity_id','entity_name','action',
                            'field_name','old_value','new_value','changed_by',
                            'changed_by_name','source_ip','user_agent','notes','changed_at'])
        EXCEPT SELECT column_name FROM information_schema.columns WHERE table_name='master_data_audit_log'
    LOOP RAISE EXCEPTION 'V215: master_data_audit_log.% belum terbentuk', v_missing; END LOOP;

    FOR v_missing IN
        SELECT unnest(ARRAY['id','tenant_id','name','abbreviation','is_system'])
        EXCEPT SELECT column_name FROM information_schema.columns WHERE table_name='product_units'
    LOOP RAISE EXCEPTION 'V215: product_units.% belum terbentuk', v_missing; END LOOP;

    FOR v_missing IN
        SELECT unnest(ARRAY['tenant_id','last_number'])
        EXCEPT SELECT column_name FROM information_schema.columns WHERE table_name='journal_sequences'
    LOOP RAISE EXCEPTION 'V215: journal_sequences.% belum terbentuk', v_missing; END LOOP;

    FOR v_missing IN
        SELECT unnest(ARRAY['turn_id','tool_call_id','tool_name','retry_attempt','status',
                            'latency_ms','error_type','idempotency_key','fallback_used','created_at'])
        EXCEPT SELECT column_name FROM information_schema.columns WHERE table_name='tool_call_logs'
    LOOP RAISE EXCEPTION 'V215: tool_call_logs.% belum terbentuk', v_missing; END LOOP;

    RAISE NOTICE 'V215 OK: master_data_audit_log + product_units + journal_sequences + tool_call_logs';
END $$;
