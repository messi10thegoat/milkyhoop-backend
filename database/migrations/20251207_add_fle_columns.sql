-- ============================================
-- Field-Level Encryption (FLE) Migration
-- MilkyHoop Security Enhancement
-- ============================================
-- This migration adds encrypted columns for PII data
-- Following UU PDP and PCI-DSS requirements
-- ============================================

BEGIN;

-- ============================================
-- 1. Create encryption metadata table
-- ============================================
CREATE TABLE IF NOT EXISTS encryption_metadata (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_id VARCHAR(100) NOT NULL,
    key_version INT NOT NULL DEFAULT 1,
    algorithm VARCHAR(50) NOT NULL DEFAULT 'AES-256-GCM',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rotated_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT unique_active_key UNIQUE (key_id, key_version)
);

-- Index for active key lookup
CREATE INDEX IF NOT EXISTS idx_encryption_metadata_active
ON encryption_metadata(is_active) WHERE is_active = true;

-- ============================================
-- 2. Create key rotation audit log
-- ============================================
CREATE TABLE IF NOT EXISTS key_rotation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL,
    old_key_id VARCHAR(100),
    new_key_id VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    total_records INT NOT NULL DEFAULT 0,
    processed_records INT NOT NULL DEFAULT 0,
    failed_records INT NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for job lookup
CREATE INDEX IF NOT EXISTS idx_key_rotation_log_job
ON key_rotation_log(job_id);

CREATE INDEX IF NOT EXISTS idx_key_rotation_log_status
ON key_rotation_log(status, created_at DESC);

-- ============================================
-- 3. Add encrypted columns to User table
-- Note: Original columns kept for migration period
-- ============================================

-- Add encrypted email column
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS email_encrypted TEXT;

-- Add encrypted name columns
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS name_encrypted TEXT;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS fullname_encrypted TEXT;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS nickname_encrypted TEXT;

-- Add blind index columns for searchable encryption
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS email_blind_index VARCHAR(64);

-- Index for blind index search
CREATE INDEX IF NOT EXISTS idx_user_email_blind_index
ON "User"(email_blind_index) WHERE email_blind_index IS NOT NULL;

-- ============================================
-- 4. Add encrypted columns to UserProfile
-- ============================================

ALTER TABLE "UserProfile" ADD COLUMN IF NOT EXISTS phone_number_encrypted TEXT;
ALTER TABLE "UserProfile" ADD COLUMN IF NOT EXISTS digital_signature_encrypted TEXT;

-- Blind index for phone search
ALTER TABLE "UserProfile" ADD COLUMN IF NOT EXISTS phone_blind_index VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_userprofile_phone_blind_index
ON "UserProfile"(phone_blind_index) WHERE phone_blind_index IS NOT NULL;

-- ============================================
-- 5. Add encrypted columns to UserBusiness
-- ============================================

ALTER TABLE "UserBusiness" ADD COLUMN IF NOT EXISTS tax_id_encrypted TEXT;
ALTER TABLE "UserBusiness" ADD COLUMN IF NOT EXISTS business_license_encrypted TEXT;

-- ============================================
-- 6. Add encrypted columns to TransaksiHarian
-- ============================================

ALTER TABLE transaksi_harian ADD COLUMN IF NOT EXISTS nama_pihak_encrypted TEXT;
ALTER TABLE transaksi_harian ADD COLUMN IF NOT EXISTS kontak_pihak_encrypted TEXT;

-- Blind index for party name search
ALTER TABLE transaksi_harian ADD COLUMN IF NOT EXISTS nama_pihak_blind_index VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_transaksi_nama_pihak_blind_index
ON transaksi_harian(nama_pihak_blind_index) WHERE nama_pihak_blind_index IS NOT NULL;

-- ============================================
-- 7. Add encrypted columns to Supplier
-- ============================================

ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS kontak_encrypted TEXT;

-- ============================================
-- 8. Add encrypted columns to Order
-- ============================================

ALTER TABLE "Order" ADD COLUMN IF NOT EXISTS customer_name_encrypted TEXT;

-- ============================================
-- 9. Create function for blind index generation
-- ============================================
CREATE OR REPLACE FUNCTION generate_blind_index(
    p_value TEXT,
    p_salt TEXT DEFAULT 'milkyhoop-blind-index-salt'
) RETURNS VARCHAR(64) AS $$
DECLARE
    v_normalized TEXT;
    v_hash TEXT;
BEGIN
    IF p_value IS NULL OR p_value = '' THEN
        RETURN NULL;
    END IF;

    -- Normalize: lowercase, trim
    v_normalized := LOWER(TRIM(p_value));

    -- Generate HMAC-SHA256
    v_hash := encode(
        hmac(v_normalized::bytea, p_salt::bytea, 'sha256'),
        'hex'
    );

    RETURN v_hash;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================
-- 10. Create migration status table
-- ============================================
CREATE TABLE IF NOT EXISTS fle_migration_status (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name VARCHAR(100) NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    total_records INT NOT NULL DEFAULT 0,
    migrated_records INT NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,

    UNIQUE(table_name, field_name)
);

-- Insert migration targets
INSERT INTO fle_migration_status (table_name, field_name)
VALUES
    ('User', 'email'),
    ('User', 'name'),
    ('User', 'fullname'),
    ('User', 'nickname'),
    ('UserProfile', 'phoneNumber'),
    ('UserProfile', 'digitalSignature'),
    ('UserBusiness', 'taxId'),
    ('UserBusiness', 'businessLicense'),
    ('transaksi_harian', 'nama_pihak'),
    ('transaksi_harian', 'kontak_pihak'),
    ('suppliers', 'kontak'),
    ('Order', 'customer_name')
ON CONFLICT (table_name, field_name) DO NOTHING;

-- ============================================
-- 11. Add encryption_version column for tracking
-- ============================================

-- Track which encryption version each record uses
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS encryption_version INT DEFAULT 0;
ALTER TABLE "UserProfile" ADD COLUMN IF NOT EXISTS encryption_version INT DEFAULT 0;
ALTER TABLE "UserBusiness" ADD COLUMN IF NOT EXISTS encryption_version INT DEFAULT 0;
ALTER TABLE transaksi_harian ADD COLUMN IF NOT EXISTS encryption_version INT DEFAULT 0;
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS encryption_version INT DEFAULT 0;
ALTER TABLE "Order" ADD COLUMN IF NOT EXISTS encryption_version INT DEFAULT 0;

COMMIT;

-- ============================================
-- NOTES FOR MIGRATION PROCEDURE:
-- ============================================
-- 1. Run this migration first to add columns
-- 2. Deploy application with FLE enabled
-- 3. Run batch migration script to encrypt existing data
-- 4. After verification period (e.g., 7 days):
--    - Remove original plaintext columns
--    - Rename encrypted columns to original names
-- ============================================
