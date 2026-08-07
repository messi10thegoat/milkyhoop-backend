-- Balikan V222. Melepas constraint saja; data yang sudah dinormalisasi
-- DIBIARKAN (mengembalikannya ke huruf kecil akan menghidupkan lagi bug
-- yang justru diperbaiki batch ini).
BEGIN;
ALTER TABLE user_tenant_roles DROP CONSTRAINT IF EXISTS user_tenant_roles_status_check;
COMMIT;
