-- ============================================================================
-- V214 — recover 2 USER-FACING missing tables (recovery drift, MISSING-TABLE class)
-- ----------------------------------------------------------------------------
-- Same class as V212 (withholding_tax_records) / V213 (chat_attachments): tables
-- the CODE writes/reads but that NO migration ever CREATEs. Found by a code-vs-DB
-- write-target scan (INSERT/UPDATE/DELETE targets absent from pg_class). The
-- 264-table parity check could not catch them: it compares dryrun-vs-live, and a
-- table missing from BOTH passes. ARBITER = CODE (never the contaminated old DB;
-- its dump is data-only anyway).
--
-- (1) sales_invoice_attachments — Lampiran di Faktur Penjualan.
--     Referenced (STATIC) in api_gateway/app/routers/sales_invoices.py:
--       INSERT :4452 (id, invoice_id, tenant_id, filename, file_path, file_size,
--                     mime_type, uploaded_by)
--       SELECT :4506/:4591 (+ uploaded_at, ORDER BY uploaded_at DESC)
--       DELETE :4571
--     Missing → GET/POST/DELETE /api/sales-invoices/{id}/attachments 500.
--     Types: id UUID (uuid4), invoice_id UUID (sales_invoices.id is uuid),
--            uploaded_by UUID (= user_id; joined via uploaded_by::text = "User".id),
--            file_size int (len(content)), rest TEXT, uploaded_at TIMESTAMPTZ.
--
-- (2) user_explicit_preferences — Tier-2 chat memory ("panggil saya X").
--     Referenced (STATIC) in
--       api_gateway/app/services/unified_agent/preference_manager.py:
--       INSERT :122 (tenant_id, user_id, key, value::jsonb, source, set_at, last_used_at)
--         ON CONFLICT (tenant_id, user_id, key) DO UPDATE  → composite PK.
--       SELECT :60 (key, value, source, set_at, last_used_at, expires_at)
--       DELETE :158/:178, UPDATE :212 (last_used_at).
--     Missing → preference set/get/list 500 (or silent-fail) on every chat turn
--     that touches Tier-2 memory.
--     Types: tenant_id TEXT, user_id UUID (sibling chat_*.user_id is uuid; asyncpg
--            encodes the str param), key TEXT, value JSONB, source TEXT,
--            set_at/last_used_at/expires_at TIMESTAMPTZ.
--
-- No FK / no RLS — consistent with V211/V212/V213 and the recovered sibling
-- tables (relrowsecurity=false; gateway connects BYPASSRLS, Law 24). Tenant
-- isolation upheld by app-layer WHERE tenant_id = $1 (present in every query).
-- Idempotent (IF NOT EXISTS) → fresh-install / re-run is a no-op.
-- ============================================================================

-- (1) ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_invoice_attachments (
    id          UUID PRIMARY KEY,
    invoice_id  UUID        NOT NULL,
    tenant_id   TEXT        NOT NULL,
    filename    TEXT,
    file_path   TEXT,
    file_size   BIGINT,
    mime_type   TEXT,
    uploaded_by UUID,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sales_invoice_attachments_invoice
    ON sales_invoice_attachments (invoice_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_invoice_attachments_tenant
    ON sales_invoice_attachments (tenant_id);

-- (2) ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_explicit_preferences (
    tenant_id    TEXT        NOT NULL,
    user_id      UUID        NOT NULL,
    key          TEXT        NOT NULL,
    value        JSONB,
    source       TEXT,
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, user_id, key)
);

-- Verify the column contracts the code needs.
DO $$
DECLARE
    v_missing TEXT;
BEGIN
    FOR v_missing IN
        SELECT unnest(ARRAY['id','invoice_id','tenant_id','filename','file_path',
                            'file_size','mime_type','uploaded_by','uploaded_at'])
        EXCEPT SELECT column_name FROM information_schema.columns
        WHERE table_name = 'sales_invoice_attachments'
    LOOP RAISE EXCEPTION 'V214: sales_invoice_attachments.% belum terbentuk', v_missing; END LOOP;

    FOR v_missing IN
        SELECT unnest(ARRAY['tenant_id','user_id','key','value','source',
                            'set_at','last_used_at','expires_at'])
        EXCEPT SELECT column_name FROM information_schema.columns
        WHERE table_name = 'user_explicit_preferences'
    LOOP RAISE EXCEPTION 'V214: user_explicit_preferences.% belum terbentuk', v_missing; END LOOP;

    RAISE NOTICE 'V214 OK: sales_invoice_attachments + user_explicit_preferences';
END $$;
