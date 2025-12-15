-- ============================================
-- V004: QR Login System Tables
-- WhatsApp-style device linking for MilkyHoop
-- ============================================

-- ============================================
-- TABLE 1: qr_login_tokens
-- Temporary tokens for QR-based login flow
-- TTL: 2 minutes, one-time use
-- ============================================
CREATE TABLE IF NOT EXISTS qr_login_tokens (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    token VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',

    -- Web device info (captured at QR generation)
    web_fingerprint VARCHAR(255),
    web_user_agent TEXT,
    web_ip VARCHAR(45),

    -- Mobile approval info
    approved_by_user_id TEXT,
    approved_by_tenant_id TEXT,
    approved_at TIMESTAMP WITH TIME ZONE,

    -- Lifecycle timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Constraints
    CONSTRAINT chk_qr_status CHECK (status IN ('pending', 'scanned', 'approved', 'rejected', 'expired'))
);

-- Indexes for qr_login_tokens
CREATE INDEX IF NOT EXISTS idx_qr_login_tokens_token ON qr_login_tokens(token);
CREATE INDEX IF NOT EXISTS idx_qr_login_tokens_status_expires ON qr_login_tokens(status, expires_at);

-- ============================================
-- TABLE 2: user_devices
-- Linked devices registry for multi-device login
-- Mobile = primary, Web = extended display
-- ============================================
CREATE TABLE IF NOT EXISTS user_devices (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,

    -- Device identification
    device_type VARCHAR(20) NOT NULL,
    device_name VARCHAR(255),
    device_fingerprint VARCHAR(255),
    user_agent TEXT,

    -- Authentication link
    refresh_token_hash VARCHAR(255),

    -- Status flags
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,

    -- Activity tracking
    last_active_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_ip VARCHAR(45),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,

    -- Foreign key constraints
    CONSTRAINT fk_user_devices_user FOREIGN KEY (user_id)
        REFERENCES "User"(id) ON DELETE CASCADE,
    CONSTRAINT fk_user_devices_tenant FOREIGN KEY (tenant_id)
        REFERENCES "Tenant"(id) ON DELETE CASCADE,

    -- Business rules
    CONSTRAINT chk_device_type CHECK (device_type IN ('mobile', 'web'))
);

-- Indexes for user_devices
CREATE INDEX IF NOT EXISTS idx_user_devices_user_tenant ON user_devices(user_id, tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_devices_fingerprint ON user_devices(device_fingerprint);
CREATE INDEX IF NOT EXISTS idx_user_devices_active ON user_devices(is_active);
CREATE INDEX IF NOT EXISTS idx_user_devices_refresh_token ON user_devices(refresh_token_hash);

-- ============================================
-- CLEANUP FUNCTION: Auto-expire QR tokens
-- Call via cron every 5 minutes
-- ============================================
CREATE OR REPLACE FUNCTION cleanup_expired_qr_tokens()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM qr_login_tokens
    WHERE expires_at < NOW()
    AND status = 'pending';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- HELPER FUNCTION: Deactivate all web devices for user
-- Used when mobile logs out (cascade logout)
-- ============================================
CREATE OR REPLACE FUNCTION deactivate_web_devices(p_user_id TEXT, p_tenant_id TEXT)
RETURNS INTEGER AS $$
DECLARE
    deactivated_count INTEGER;
BEGIN
    UPDATE user_devices
    SET is_active = FALSE,
        expires_at = NOW()
    WHERE user_id = p_user_id
    AND tenant_id = p_tenant_id
    AND device_type = 'web'
    AND is_active = TRUE;

    GET DIAGNOSTICS deactivated_count = ROW_COUNT;
    RETURN deactivated_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- COMMENTS
-- ============================================
COMMENT ON TABLE qr_login_tokens IS 'Temporary tokens for QR-based login (WhatsApp-style)';
COMMENT ON TABLE user_devices IS 'Linked devices registry for multi-device authentication';
COMMENT ON FUNCTION cleanup_expired_qr_tokens() IS 'Cleanup expired QR tokens - call via cron';
COMMENT ON FUNCTION deactivate_web_devices(TEXT, TEXT) IS 'Deactivate all web sessions for a user (cascade logout)';
