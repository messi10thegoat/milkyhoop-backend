-- ROLLBACK V450: kode services/pengingat.ambang_segera HARUS dikembalikan dulu (ia membaca kolom ini).
ALTER TABLE accounting_settings DROP CONSTRAINT IF EXISTS chk_pengingat_segera_hari;
ALTER TABLE accounting_settings DROP COLUMN IF EXISTS pengingat_segera_hari;
