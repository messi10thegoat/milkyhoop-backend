-- V223 — persempit user_tenant_roles.status menjadi ('ACTIVE','SUSPENDED')
--
-- KENAPA MENYUSUL V222 SECEPAT INI
-- --------------------------------
-- V222 (kemarin) memasang CHECK dengan TIGA nilai: ACTIVE, INACTIVE, SUSPENDED.
-- Audit sesudahnya menemukan bahwa 'INACTIVE' dan 'SUSPENDED' TIDAK DIPAKAI di
-- mana pun dalam kode Python — nol kemunculan di seluruh backend/. Artinya V222
-- memperkenalkan dua nilai TANPA ATURAN PEMAKAIAN.
--
-- Itu persis kelas cacat yang baru saja kita tutup. Ketidakcocokan huruf besar-
-- kecil ('active' vs 'ACTIVE') terjadi karena dua penulis memilih bentuk berbeda
-- tanpa ada yang memutuskan bentuk mana yang benar. Nilai tanpa aturan pemakaian
-- adalah undangan bagi hal yang sama untuk terulang: satu penulis menulis
-- 'INACTIVE', pembaca lain hanya mengenali 'SUSPENDED'.
--
-- Perbedaan makna antara "nonaktif" dan "ditangguhkan" tidak pernah ditetapkan
-- dan tidak dibutuhkan produk. Daripada mengarang perbedaan supaya nilainya
-- terpakai, nilainya yang dihapus.
--
-- Data: 100% baris berstatus 'ACTIVE' saat migrasi ini ditulis — normalisasi
-- nol baris. UPDATE di bawah tetap ada demi idempotensi, bukan karena diharapkan
-- mengenai sesuatu.

BEGIN;

-- Jaring pengaman: kalau ada baris 'INACTIVE' yang lahir di sela V222 dan V223,
-- ia dipetakan ke SUSPENDED (satu-satunya makna "tidak aktif" yang tersisa).
UPDATE user_tenant_roles
SET status = 'SUSPENDED'
WHERE upper(status) = 'INACTIVE';

ALTER TABLE user_tenant_roles
  DROP CONSTRAINT IF EXISTS user_tenant_roles_status_check;

ALTER TABLE user_tenant_roles
  ADD CONSTRAINT user_tenant_roles_status_check
  CHECK (status IN ('ACTIVE', 'SUSPENDED'));

COMMIT;
