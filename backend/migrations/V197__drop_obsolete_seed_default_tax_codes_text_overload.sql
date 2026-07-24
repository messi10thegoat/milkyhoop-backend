-- ============================================================================
-- V197__drop_obsolete_seed_default_tax_codes_text_overload.sql
--
-- BUG LATEN, terungkap saat E2E signup di DB murni-hasil-resep (2026-07-24):
--   POST /api/auth/signup/complete-setup -> 500
--   "new row for relation tax_codes violates check constraint
--    tax_codes_ppn_direction_check"
--
-- AKAR: V025 mendefinisikan seed_default_tax_codes(p_tenant_id TEXT).
-- V167 bermaksud MENGGANTINYA dengan versi yang mengisi `direction`, tapi
-- menulis parameternya VARCHAR. Di PostgreSQL, CREATE OR REPLACE FUNCTION
-- dengan tipe argumen berbeda membuat OVERLOAD BARU, bukan pengganti.
-- Akibatnya kedua versi hidup berdampingan, dan pemanggilan
-- `SELECT seed_default_tax_codes($1)` dari onboarding_service.py:151
-- me-resolve ke overload TEXT yang lama — yang TIDAK mengisi `direction`
-- sehingga melanggar CHECK yang dipasang V167 itu sendiri.
--
-- DAMPAK: setiap signup tenant baru GAGAL 500. Tidak pernah ketahuan karena
-- E2E sebelumnya mem-bypass signup (membuat tenant langsung lewat SQL).
--
-- FIX: buang overload TEXT yang usang. Versi VARCHAR dari V167 tetap dipakai;
-- pemanggilan dengan argumen text akan di-resolve ke sana lewat cast implisit.
-- ============================================================================

BEGIN;

DROP FUNCTION IF EXISTS public.seed_default_tax_codes(text);

DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT COUNT(*) INTO v_count FROM pg_proc WHERE proname = 'seed_default_tax_codes';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'seed_default_tax_codes harus tersisa TEPAT 1 overload, ditemukan %', v_count;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- KOREKSI atas V195: tax_codes.coretax_tax_code TERNYATA DIPERLUKAN.
--
-- V195 membuangnya karena grep ke routers/ services/ schemas/ (Python)
-- menghasilkan nol referensi. Itu ARBITER YANG TERLALU SEMPIT: kolom ini
-- ditulis oleh FUNGSI SQL, bukan kode Python —
-- V167:76 INSERT INTO tax_codes (..., coretax_tax_code) VALUES ...
-- di dalam seed_default_tax_codes(), yang dipanggil saat signup.
--
-- Pelajaran: arbiter skema = kode Python DAN fungsi/migrasi SQL.
-- Terungkap oleh E2E signup di DB murni-hasil-resep, bukan oleh analisis statik.
-- ---------------------------------------------------------------------------
ALTER TABLE tax_codes ADD COLUMN IF NOT EXISTS coretax_tax_code TEXT;

COMMIT;
