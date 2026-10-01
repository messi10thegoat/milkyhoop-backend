-- V353 (1 Okt 2026, putusan pemilik LANGSUNG di sesi BACKEND, AskUserQuestion: "Basic settings + team"):
-- preset sistem ADMIN = akses penuh SEMUA modul bisnis, KECUALI infrastruktur:
--   SETTINGS        {R,U}         -> {R}  (pengaturan dasar: /api/settings*, profil & logo usaha)
--   USER_MANAGEMENT {C,R,U,A,P}   -> {R}  (undang, ubah peran, Kelola Akses, hapus anggota)
-- Modul bisnis ADMIN sudah {C,R,U,D,V,A,P,E} sejak V127 (diukur: satu-satunya celah pola rute = team_management D).
-- Mengubah PRESET peran (bukan "User".role, bukan hibah per-pengguna). Berlaku untuk SEMUA tenant yang punya
-- anggota ADMIN (diukur 1 Okt: 2 -- grapgrap-manado, ponte-publishing; 0 user_permission_overrides untuk ADMIN).
-- Cache izin PolicyEngine TAK punya pembatalan -> berlaku sesudah gateway di-restart.
UPDATE role_permissions rp SET actions = '{R}'::char[]
FROM roles r
WHERE r.id = rp.role_id AND r.code = 'ADMIN' AND r.tenant_id = '__SYSTEM__'
  AND rp.module IN ('SETTINGS', 'USER_MANAGEMENT');

UPDATE roles SET description = 'Akses penuh semua modul bisnis; pengaturan dasar dan tim hanya lihat'
WHERE code = 'ADMIN' AND tenant_id = '__SYSTEM__';

DO $$
BEGIN
    IF (SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
        WHERE r.code = 'ADMIN' AND r.tenant_id = '__SYSTEM__' AND rp.module IN ('SETTINGS', 'USER_MANAGEMENT')
          AND rp.actions = '{R}'::char[]) <> 2 THEN
        RAISE EXCEPTION 'V353: preset ADMIN SETTINGS/USER_MANAGEMENT tidak mendarat sebagai {R}';
    END IF;
END $$;
