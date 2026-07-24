-- ============================================================================
-- V216 — re-apply V057 tables that ABORTED mid-file during recovery
-- ----------------------------------------------------------------------------
-- Unlike V212–V215 (tables with NO DDL anywhere), these 3 DO have authoritative
-- DDL in V057__audit_trail.sql — but V057 aborted after CREATE TABLE audit_logs
-- (audit_logs exists; sensitive_data_access / login_history /
-- audit_retention_policies do NOT). So the recovery run's "V057 OK" was really a
-- partial apply. DDL below is COPIED VERBATIM from V057 (arbiter here = the
-- migration itself, the source of truth) and made idempotent so a fresh-install
-- re-run is a no-op.
--
-- RLS + policies kept faithful to V057 (decorative for gateway BYPASSRLS traffic,
-- Law 24, but honest to the source). audit_logs itself already exists and is not
-- touched here.
-- ============================================================================

CREATE TABLE IF NOT EXISTS sensitive_data_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    access_time TIMESTAMPTZ DEFAULT NOW(),
    user_id UUID NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    entity_type VARCHAR(100),
    entity_id UUID,
    reason TEXT,
    authorized_by UUID,
    was_exported BOOLEAN DEFAULT false,
    export_format VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS login_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    user_id UUID NOT NULL,
    user_email VARCHAR(255),
    session_id UUID,
    login_time TIMESTAMPTZ DEFAULT NOW(),
    logout_time TIMESTAMPTZ,
    ip_address INET,
    user_agent TEXT,
    device_type VARCHAR(50),
    location_country VARCHAR(100),
    location_city VARCHAR(100),
    login_status VARCHAR(20),
    failure_reason TEXT,
    is_suspicious BOOLEAN DEFAULT false,
    mfa_used BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS audit_retention_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    category VARCHAR(50) NOT NULL,
    retention_days INTEGER NOT NULL DEFAULT 365,
    archive_after_days INTEGER,
    delete_after_days INTEGER,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_retention_policy UNIQUE(tenant_id, category)
);

-- RLS (verbatim from V057), idempotent.
ALTER TABLE sensitive_data_access    ENABLE ROW LEVEL SECURITY;
ALTER TABLE login_history            ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_retention_policies ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_sensitive_data_access ON sensitive_data_access;
CREATE POLICY rls_sensitive_data_access ON sensitive_data_access
    USING (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS rls_login_history ON login_history;
CREATE POLICY rls_login_history ON login_history
    USING (tenant_id = current_setting('app.tenant_id', true) OR tenant_id IS NULL);
DROP POLICY IF EXISTS rls_audit_retention_policies ON audit_retention_policies;
CREATE POLICY rls_audit_retention_policies ON audit_retention_policies
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Indexes (verbatim from V057), idempotent.
CREATE INDEX IF NOT EXISTS idx_sensitive_access_time ON sensitive_data_access(tenant_id, access_time DESC);
CREATE INDEX IF NOT EXISTS idx_sensitive_access_user ON sensitive_data_access(user_id, access_time DESC);
CREATE INDEX IF NOT EXISTS idx_login_history_user ON login_history(user_id, login_time DESC);
CREATE INDEX IF NOT EXISTS idx_login_history_status ON login_history(login_status) WHERE login_status != 'success';
CREATE INDEX IF NOT EXISTS idx_login_history_suspicious ON login_history(is_suspicious) WHERE is_suspicious = true;

DO $$
BEGIN
    IF to_regclass('public.sensitive_data_access') IS NULL
       OR to_regclass('public.login_history') IS NULL
       OR to_regclass('public.audit_retention_policies') IS NULL THEN
        RAISE EXCEPTION 'V216: satu/lebih tabel audit V057 belum terbentuk';
    END IF;
    RAISE NOTICE 'V216 OK: sensitive_data_access + login_history + audit_retention_policies (V057 tail)';
END $$;
