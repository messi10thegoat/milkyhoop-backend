-- Balikkan V223 -> constraint V222 (tiga nilai).
-- CATATAN: baris 'INACTIVE' yang sudah dipetakan ke 'SUSPENDED' TIDAK dipulihkan
-- — informasinya tak tersimpan di mana pun. Saat V223 ditulis, jumlah baris
-- semacam itu = 0, jadi pembalikan ini lossless dalam praktiknya.
BEGIN;
ALTER TABLE user_tenant_roles DROP CONSTRAINT IF EXISTS user_tenant_roles_status_check;
ALTER TABLE user_tenant_roles ADD CONSTRAINT user_tenant_roles_status_check
  CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED'));
COMMIT;
