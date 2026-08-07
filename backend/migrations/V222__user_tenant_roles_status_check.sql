-- V222 — kunci bentuk user_tenant_roles.status di SKEMA.
--
-- KENAPA CONSTRAINT, BUKAN CATATAN.
-- Skill milkyhoop-team-access SUDAH mencantumkan case-mismatch sebagai gotcha
-- nomor SATU, dengan instruksi eksplisit "selalu LOWER(status)" — dan cacatnya
-- tetap masuk produksi lalu bertahan dua bulan (2026-06-03 s/d 2026-08-07),
-- membuat setiap login melewati cabang tebakan alih-alih lookup.
--   Konvensi bocor. Skema tidak.
-- Argumen sama dengan UNIQUE V218 dan PK V221.
--
-- URUTAN WAJIB: penulis diseragamkan LEBIH DULU (invite_public.py 'active' ->
-- 'ACTIVE', commit yang sama). Memasang constraint sebelum itu akan mem-500-kan
-- jalur invite begitu invite diperbaiki — instance baru dari pola
-- "fix mereproduksi diri di lapisan tetangga".
--
-- Bentuk kanonik = DEFAULT kolom ('ACTIVE', V195:232). Kode tidak memilih;
-- skema yang memutuskan.
--
-- Bukti pra-migrasi (query, bukan penalaran):
--   SELECT status, count(*) FROM user_tenant_roles GROUP BY 1;  -> ACTIVE | 1
--   SELECT count(*) FROM user_tenant_roles
--     WHERE status NOT IN ('ACTIVE','INACTIVE','SUSPENDED');    -> 0
-- Normalisasi karena itu nol baris; UPDATE di bawah tetap ada supaya migrasi
-- ini aman dijalankan di basis data lain yang mungkin memuat huruf kecil.

BEGIN;

-- 1. Normalisasi apa pun yang menyimpang (idempoten, nol baris di milkydb).
UPDATE user_tenant_roles
SET status = UPPER(TRIM(status))
WHERE status IS DISTINCT FROM UPPER(TRIM(status));

-- 2. Nilai di luar himpunan yang dikenal -> ACTIVE, dan CATAT.
--    (Tak ada di milkydb; jaring untuk basis data lain.)
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM user_tenant_roles
  WHERE status NOT IN ('ACTIVE','INACTIVE','SUSPENDED');
  IF n > 0 THEN
    RAISE NOTICE 'V222: % baris ber-status tak dikenal dinormalisasi ke ACTIVE', n;
    UPDATE user_tenant_roles SET status = 'ACTIVE'
    WHERE status NOT IN ('ACTIVE','INACTIVE','SUSPENDED');
  END IF;
END $$;

-- 3. Pasang constraint (idempoten).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'user_tenant_roles'::regclass
      AND conname = 'user_tenant_roles_status_check'
  ) THEN
    ALTER TABLE user_tenant_roles
      ADD CONSTRAINT user_tenant_roles_status_check
      CHECK (status IN ('ACTIVE','INACTIVE','SUSPENDED'));
    RAISE NOTICE 'V222: constraint user_tenant_roles_status_check dipasang';
  ELSE
    RAISE NOTICE 'V222: constraint sudah ada, dilewati';
  END IF;
END $$;

COMMIT;
