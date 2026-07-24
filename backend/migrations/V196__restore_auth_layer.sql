-- ============================================================================
-- V196__restore_auth_layer.sql
--
-- Melengkapi lapisan auth/onboarding yang tidak dihasilkan resep. Tanpa ini
-- SIGNUP GAGAL, sehingga E2E dari nol mustahil dijalankan.
--
-- ARBITER (bukan isi milkydb):
--   - libs/milkyhoop_prisma/schema.prisma  -> model User, Tenant, RefreshToken
--   - services/onboarding_service.py:93,116 -> INSERT "Tenant" / "User"
--   - routers/signup.py:141-258            -> pending_registrations
--   - routers/production.py:2221-2293      -> production_completions.is_overrun
--
-- CATATAN: milkydb TIDAK dipakai sebagai referensi. Dua cacat di milkydb
-- justru TIDAK direplikasi di sini:
--   1. pending_registrations di milkydb TIDAK punya attempt_count, padahal
--      signup.py membacanya dan menaikkannya (verify_code). Jalur verifikasi
--      email tidak pernah teruji karena E2E mem-bypass-nya.
--   2. refresh_tokens di milkydb hanya punya PK + unique(token_hash); Prisma
--      mendeklarasikan 3 index. Dua yang hilang ditambahkan di sini.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Enum "Role" — WAJIB ADA sebelum kolom User.role.
--    onboarding_service.py:121 melakukan cast eksplisit 'ADMIN'::"Role".
--    Nilai diambil dari enum Role di schema.prisma:874.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'Role') THEN
        CREATE TYPE "Role" AS ENUM ('FREE','BUSINESS','PRO','CORPORATE','ADMIN');
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Kolom "User" + "Tenant" yang ditulis onboarding_service (signup).
--    Tipe/default diambil dari struktur, daftar kolom dari kode+Prisma.
-- ---------------------------------------------------------------------------
ALTER TABLE "production_completions" ADD COLUMN IF NOT EXISTS "is_overrun" boolean DEFAULT false;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "country" text DEFAULT 'ID'::text;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "currency" text DEFAULT 'IDR'::text;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "fiscal_year_start" integer DEFAULT 1;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "max_transactions_per_month" integer DEFAULT 1000;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "max_users" integer DEFAULT 3;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "plan_tier" text DEFAULT 'BASE'::text;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS "timezone" text DEFAULT 'Asia/Jakarta'::text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "avatarUrl" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "bio" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "coverPhotoUrl" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "createdAt" timestamp with time zone DEFAULT now();
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "emailVerified" timestamp with time zone;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "isVerified" boolean DEFAULT false NOT NULL;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "last_active_tenant_id" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "lastInteraction" timestamp with time zone DEFAULT now();
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "nickname" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "oauthId" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "oauthProvider" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "passwordHash" text;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "role" "Role" DEFAULT 'FREE'::"Role" NOT NULL;
ALTER TABLE "User" ADD COLUMN IF NOT EXISTS "updatedAt" timestamp with time zone DEFAULT now();

-- ---------------------------------------------------------------------------
-- 3. refresh_tokens — model RefreshToken (schema.prisma:83).
--    3 index sesuai deklarasi Prisma @@index.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id      TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    revoked_at   TIMESTAMPTZ,
    device_info  TEXT,
    last_used_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_tenant ON refresh_tokens (user_id, tenant_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires     ON refresh_tokens (expires_at);

-- ---------------------------------------------------------------------------
-- 4. pending_registrations — arbiter routers/signup.py.
--    attempt_count WAJIB: dibaca di :211 (SELECT) dan dinaikkan di :243.
--    Kolom ini TIDAK ADA di milkydb -> verify_code di sana akan error.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_registrations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             TEXT NOT NULL,
    verification_code TEXT NOT NULL,
    magic_token       TEXT,
    expires_at        TIMESTAMPTZ NOT NULL,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
    verified_at       TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ
);
ALTER TABLE pending_registrations ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_pending_reg_email  ON pending_registrations (email, status);
CREATE INDEX IF NOT EXISTS idx_pending_reg_magic  ON pending_registrations (magic_token) WHERE magic_token IS NOT NULL;

COMMIT;
