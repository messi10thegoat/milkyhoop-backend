-- ROLLBACK V329: kode yang membaca kolom ini WAJIB dikembalikan DULU (detail bank memakai ba.*; helper cetak memilih kolom).
ALTER TABLE bank_accounts DROP COLUMN IF EXISTS account_holder_name;
