-- Fixture uji deactivate. Satu anggota CASHIER berstatus ACTIVE.
DELETE FROM user_permission_overrides WHERE user_id IN (SELECT id FROM "User" WHERE email LIKE 'deact+%');
DELETE FROM user_tenant_roles WHERE user_id IN (SELECT id::uuid FROM "User" WHERE email LIKE 'deact+%');
DELETE FROM "User" WHERE email LIKE 'deact+%';

INSERT INTO "User" (id, email, name, "passwordHash", "isVerified", role, "tenantId", "createdAt", "updatedAt")
SELECT gen_random_uuid()::text, 'deact+kasir@kaosbiru.co.id', 'Deact Kasir',
       u."passwordHash", true, u.role, u."tenantId", now(), now()
FROM "User" u WHERE u.email='owner@kaosbiru.co.id';

INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, is_primary, status)
SELECT u.id::uuid, 'kaos-biru-konveksi', r.id, true, 'ACTIVE'
FROM "User" u, roles r
WHERE u.email='deact+kasir@kaosbiru.co.id' AND r.code='CASHIER';

SELECT u.email, r.code, utr.status FROM user_tenant_roles utr
JOIN "User" u ON u.id::uuid=utr.user_id JOIN roles r ON r.id=utr.role_id
WHERE u.email LIKE 'deact+%';
