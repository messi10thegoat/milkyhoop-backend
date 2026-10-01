-- Rollback V353: kembalikan preset ADMIN V127.
UPDATE role_permissions rp SET actions = CASE rp.module WHEN 'SETTINGS' THEN '{R,U}'::char[] ELSE '{C,R,U,A,P}'::char[] END
FROM roles r
WHERE r.id = rp.role_id AND r.code = 'ADMIN' AND r.tenant_id = '__SYSTEM__'
  AND rp.module IN ('SETTINGS', 'USER_MANAGEMENT');
UPDATE roles SET description = 'Akses penuh kecuali pengaturan kritis dan penghapusan user'
WHERE code = 'ADMIN' AND tenant_id = '__SYSTEM__';
