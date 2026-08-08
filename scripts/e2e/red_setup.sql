-- Pengguna sekali-pakai untuk uji-merah batch permission.
-- Nol sentuhan ke baris milik owner. passwordHash disalin dari owner supaya
-- kata sandi yang sama berlaku (tak perlu bcrypt di luar).
DELETE FROM user_tenant_roles WHERE user_id IN
  (SELECT id::uuid FROM "User" WHERE email LIKE 'redtest+%@kaosbiru.co.id');
DELETE FROM "User" WHERE email LIKE 'redtest+%@kaosbiru.co.id';

INSERT INTO "User" (id, email, name, "passwordHash", "isVerified", role, "tenantId", "createdAt", "updatedAt")
SELECT gen_random_uuid()::text, 'redtest+bendahara@kaosbiru.co.id', 'Red Bendahara',
       u."passwordHash", true, u.role, u."tenantId", now(), now()
FROM "User" u WHERE u.email = 'delivered+owner@resend.dev';

INSERT INTO "User" (id, email, name, "passwordHash", "isVerified", role, "tenantId", "createdAt", "updatedAt")
SELECT gen_random_uuid()::text, 'redtest+norole@kaosbiru.co.id', 'Red NoRole',
       u."passwordHash", true, u.role, u."tenantId", now(), now()
FROM "User" u WHERE u.email = 'delivered+owner@resend.dev';

-- BENDAHARA, status 'ACTIVE' (bentuk yang ditulis onboarding_service).
INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, is_primary, status)
SELECT u.id::uuid, 'kaos-biru-konveksi', r.id, true, 'ACTIVE'
FROM "User" u, roles r
WHERE u.email = 'redtest+bendahara@kaosbiru.co.id' AND r.code = 'BENDAHARA';

INSERT INTO "User" (id, email, name, "passwordHash", "isVerified", role, "tenantId", "createdAt", "updatedAt")
SELECT gen_random_uuid()::text, 'redtest+suspended@kaosbiru.co.id', 'Red Suspended',
       u."passwordHash", true, u.role, u."tenantId", now(), now()
FROM "User" u WHERE u.email = 'delivered+owner@resend.dev';

-- Keanggotaan ADA tapi DINONAKTIFKAN. Tanpa baris ini, cabang 403 di helper
-- lahir sebagai [INFER] — kode yang tak pernah dieksekusi. Satu baris seed
-- mengubahnya jadi cabang yang benar-benar diuji.
INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, is_primary, status)
SELECT u.id::uuid, 'kaos-biru-konveksi', r.id, true, 'SUSPENDED'
FROM "User" u, roles r
WHERE u.email = 'redtest+suspended@kaosbiru.co.id' AND r.code = 'CASHIER';

-- redtest+norole SENGAJA tanpa baris user_tenant_roles (simulasi lookup miss).

-- Tenant KEDUA untuk menguji pembaca kelima (auth.py:237).
-- Cabang last_active_tenant_id hanya dieksekusi bila last_active BERBEDA dari
-- tenant utama. Dengan satu tenant, cabang itu tak pernah jalan dan bug-nya
-- tak bisa dibuktikan. Dua baris, nol jurnal, dibersihkan sesudah batch.
DELETE FROM user_tenant_roles WHERE tenant_id = 'redtest-tenant-kedua';
DELETE FROM "Tenant" WHERE id = 'redtest-tenant-kedua';
INSERT INTO "Tenant" (id, display_name, status, created_at, updated_at)
VALUES ('redtest-tenant-kedua', 'Redtest Tenant Kedua', 'active', now(), now());

INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, is_primary, status)
SELECT u.id::uuid, 'redtest-tenant-kedua', r.id, false, 'ACTIVE'
FROM "User" u, roles r
WHERE u.email = 'delivered+owner@resend.dev' AND r.code = 'OWNER' AND r.tenant_id = '__SYSTEM__';

-- last_active DIKOSONGKAN di seed, di-set hanya oleh uji 5.
-- Kalau di-set di sini, login owner (uji 0) akan BENAR-BENAR berpindah ke
-- tenant kedua — sekarang bahwa pembaca kelima sudah diperbaiki — dan token
-- owner jadi milik tenant itu, sehingga GET /team-members mengembalikan
-- daftar tenant KEDUA. Uji 2/3 lalu gagal mencari member_id.
-- (Efek samping ini justru bukti tambahan bahwa perbaikan reader-5 bekerja.)
UPDATE "User" SET last_active_tenant_id = NULL
WHERE email = 'delivered+owner@resend.dev';

SELECT u.email, COALESCE(r.code,'(nol baris)') AS peran, utr.status
FROM "User" u
LEFT JOIN user_tenant_roles utr ON utr.user_id = u.id::uuid
LEFT JOIN roles r ON r.id = utr.role_id
WHERE u.email LIKE 'redtest+%' ORDER BY 1;
