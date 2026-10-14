-- ROLLBACK V422: kembalikan kode ke versi pra-DEPOSIT-JENIS DULU (kode baru membaca kolom ini), baru jalankan ini.
ALTER TABLE customer_deposits DROP CONSTRAINT IF EXISTS chk_cust_deposit_jenis;
ALTER TABLE customer_deposits DROP COLUMN IF EXISTS jenis;
