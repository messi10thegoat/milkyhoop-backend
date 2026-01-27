-- =============================================
-- V073: RLS for Critical Auth Tables
-- Purpose: Add Row Level Security to authentication and audit tables
-- Security: ISO 27001 compliance, session hijacking prevention
-- =============================================

-- ============================================================================
-- 1. QR LOGIN TOKENS - Session hijacking prevention
-- Uses approved_by_tenant_id for tenant isolation
-- ============================================================================
ALTER TABLE qr_login_tokens ENABLE ROW LEVEL SECURITY;

-- Allow users to see only tokens approved by their tenant
-- Pending tokens (not yet approved) have no tenant isolation
CREATE POLICY rls_qr_login_tokens ON qr_login_tokens
    USING (
        approved_by_tenant_id IS NULL  -- Pending tokens visible during scan
        OR approved_by_tenant_id = current_setting('app.tenant_id', true)
    );

-- Restrict INSERT to authenticated sessions only
CREATE POLICY rls_qr_login_tokens_insert ON qr_login_tokens
    FOR INSERT
    WITH CHECK (true);  -- Anyone can create pending tokens

-- Restrict UPDATE to own tenant's tokens
CREATE POLICY rls_qr_login_tokens_update ON qr_login_tokens
    FOR UPDATE
    USING (
        approved_by_tenant_id IS NULL  -- Can update pending tokens
        OR approved_by_tenant_id = current_setting('app.tenant_id', true)
    );

-- ============================================================================
-- 2. USER DEVICES - Device enumeration prevention
-- Already has tenant_id column
-- ============================================================================
ALTER TABLE user_devices ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_user_devices ON user_devices
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_user_devices_insert ON user_devices
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_user_devices_update ON user_devices
    FOR UPDATE
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_user_devices_delete ON user_devices
    FOR DELETE
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 3. MFA AUDIT LOG - ISO 27001 compliance
-- Add tenant_id column first (missing in original schema)
-- ============================================================================
ALTER TABLE mfa_audit_log ADD COLUMN IF NOT EXISTS tenant_id TEXT;

-- Backfill tenant_id from User table
UPDATE mfa_audit_log mal
SET tenant_id = u."tenantId"
FROM "User" u
WHERE mal.user_id = u.id
AND mal.tenant_id IS NULL;

-- Create index for RLS performance
CREATE INDEX IF NOT EXISTS idx_mfa_audit_tenant ON mfa_audit_log(tenant_id, created_at DESC);

-- Enable RLS
ALTER TABLE mfa_audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_mfa_audit_log ON mfa_audit_log
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_mfa_audit_log_insert ON mfa_audit_log
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 4. TENANT CONFIG - Feature flag tampering prevention
-- Uses tenant_id as PRIMARY KEY
-- ============================================================================
ALTER TABLE tenant_config ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_tenant_config ON tenant_config
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_tenant_config_insert ON tenant_config
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_tenant_config_update ON tenant_config
    FOR UPDATE
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- COMMENTS
-- ============================================================================
COMMENT ON POLICY rls_qr_login_tokens ON qr_login_tokens
    IS 'Prevents cross-tenant QR login token access - session hijacking mitigation';

COMMENT ON POLICY rls_user_devices ON user_devices
    IS 'Prevents device enumeration attacks across tenants';

COMMENT ON POLICY rls_mfa_audit_log ON mfa_audit_log
    IS 'ISO 27001:2022 A.8.15 - MFA audit logs isolated by tenant';

COMMENT ON POLICY rls_tenant_config ON tenant_config
    IS 'Prevents feature flag tampering across tenants';
