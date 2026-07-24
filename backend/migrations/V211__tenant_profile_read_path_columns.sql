-- ============================================================================
-- V211__tenant_profile_read_path_columns.sql
--
-- BUG: halaman "Profil Bisnis" gagal fetching (GET /api/tenant/profile 500).
--      asyncpg UndefinedColumnError: column "phone" does not exist.
--
-- KELAS: DRIFT READ-PATH (SELECT) di tabel "Tenant" (Prisma-style, kuoted).
-- Sama seperti V210 (customers): tabel "Tenant" dipulihkan recovery dari
-- write-path minimal (V196 auth layer) sehingga kolom profil bisnis + logo +
-- suspensi tidak ikut terbentuk. GET crash di kolom pertama yang hilang.
--
-- AUDIT SELURUH pembaca "Tenant" (bukan hanya kolom gejala 'phone') — pelajaran
-- V210. Kolom yang DIBACA kode tapi TAK ADA di tabel:
--   phone         — SELECT + UPDATE profil; DIBACA JUGA oleh 7 endpoint PDF:
--                   deliveries:443, receive_payments:2512, customer_deposits:2567,
--                   quotes:1667, sales_invoices:3978, bills:620, tenant_profile
--   tax_id        — SELECT + UPDATE profil (NPWP)
--   logo_url      — SELECT + UPDATE profil/logo; DIBACA JUGA oleh auth:511
--                   (daftar tenant), user:85, + semua endpoint PDF di atas
--   suspended_at  — auth:243 / auth:511 / auth:552 (guard "WHERE suspended_at
--                   IS NULL" pada path daftar/pindah-tenant & suspensi).
--                   Login inti tetap jalan karena baris ini bukan di path
--                   penerbitan token (path dingin), tapi tetap 500 saat dipanggil.
--
-- Semua 4 kolom nullable, ditulis+dibaca sebagai text/timestamp; "Tenant" =
-- Prisma (semua kolom text) → phone/tax_id/logo_url TEXT, suspended_at TIMESTAMPTZ.
-- NULL = default aman (no phone / no logo / tidak disuspensi).
-- ============================================================================

BEGIN;

ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS phone        TEXT;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS tax_id       TEXT;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS logo_url     TEXT;
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;

DO $v211$
DECLARE v_missing TEXT;
BEGIN
    FOR v_missing IN
        SELECT c FROM unnest(ARRAY['phone','tax_id','logo_url','suspended_at']) AS c
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_schema='public' AND table_name='Tenant' AND column_name=c)
    LOOP
        RAISE EXCEPTION 'V211: "Tenant".% belum terbentuk', v_missing;
    END LOOP;
    RAISE NOTICE 'V211 OK: "Tenant" read-path columns (phone, tax_id, logo_url, suspended_at)';
END $v211$;

COMMIT;
