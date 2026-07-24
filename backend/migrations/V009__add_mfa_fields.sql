-- ============================================
-- MFA (Multi-Factor Authentication) Fields
-- ISO 27001:2022 - A.8.5 Secure Authentication
-- ============================================

-- Add MFA fields to UserSecurity table
ALTER TABLE "UserSecurity"
ADD COLUMN IF NOT EXISTS "totpSecret" VARCHAR(64),
ADD COLUMN IF NOT EXISTS "totpVerified" BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS "mfaBackupCodes" TEXT[],
ADD COLUMN IF NOT EXISTS "mfaEnabledAt" TIMESTAMP,
ADD COLUMN IF NOT EXISTS "lastMfaVerification" TIMESTAMP;

-- Create MFA audit log table
CREATE TABLE IF NOT EXISTS "mfa_audit_log" (
    "id" TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    "user_id" TEXT NOT NULL REFERENCES "User"("id") ON DELETE CASCADE,
    "action" VARCHAR(50) NOT NULL, -- setup, verify, disable, backup_used
    "success" BOOLEAN NOT NULL,
    "ip_address" VARCHAR(45),
    "user_agent" TEXT,
    "created_at" TIMESTAMP DEFAULT NOW()
);

-- Index for MFA audit queries
CREATE INDEX IF NOT EXISTS "idx_mfa_audit_user" ON "mfa_audit_log"("user_id", "created_at" DESC);

-- Comment for documentation
COMMENT ON TABLE "mfa_audit_log" IS 'Audit trail for MFA operations - ISO 27001:2022 A.8.15';
COMMENT ON COLUMN "UserSecurity"."totpSecret" IS 'TOTP secret key (encrypted at rest)';
COMMENT ON COLUMN "UserSecurity"."mfaBackupCodes" IS 'One-time backup codes for account recovery';
