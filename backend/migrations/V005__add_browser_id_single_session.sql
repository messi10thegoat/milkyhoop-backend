-- ============================================
-- V005: Browser-Level Single Session Enforcement
-- 1 browser profile = 1 device = 1 active session
-- WhatsApp-style single session per browser
-- ============================================

BEGIN;

-- Add browser_id column to user_devices
-- This identifies the browser profile (shared across tabs in same browser)
ALTER TABLE user_devices ADD COLUMN IF NOT EXISTS browser_id TEXT;

-- Backfill existing records with their id as browser_id
UPDATE user_devices SET browser_id = id::text WHERE browser_id IS NULL;

-- Make browser_id NOT NULL after backfill
ALTER TABLE user_devices ALTER COLUMN browser_id SET NOT NULL;

-- Index for browser_id lookups
CREATE INDEX IF NOT EXISTS idx_user_devices_browser_id ON user_devices (browser_id);

-- CRITICAL: Partial unique index for single session enforcement
-- Only ONE active session per browser per user+tenant
-- This is the database-level enforcement (final authority)
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_devices_browser_active
ON user_devices (user_id, tenant_id, browser_id)
WHERE is_active = true AND device_type = 'web';

-- Add browser_id column to qr_login_tokens
-- Stores browser_id during QR flow to pass to device registration
ALTER TABLE qr_login_tokens ADD COLUMN IF NOT EXISTS browser_id TEXT;

COMMIT;

-- ============================================
-- NOTES:
-- - browser_id comes from localStorage (survives tab closes, cleared on logout)
-- - tab_id comes from sessionStorage (unique per tab, for WebSocket)
-- - device_id is generated on device registration (identifies the session)
--
-- Flow:
-- 1. Desktop generates QR with browser_id
-- 2. Mobile approves -> backend creates device with browser_id
-- 3. If same browser_id already has active device, OLD device is kicked
-- 4. Unique index prevents race conditions (database is final authority)
-- ============================================
