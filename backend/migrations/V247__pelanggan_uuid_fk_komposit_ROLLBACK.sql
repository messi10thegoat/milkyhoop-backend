-- ROLLBACK V247 — FK komposit & UNIQUE dicabut, kedua kolom kembali VARCHAR(255).
-- Lossless: uuid::text = bentuk kanonik huruf kecil; migrasi maju sudah meng-assert semua nilai lama kanonik,
-- tanpa string kosong. NULL tetap NULL. Kode DEPLOY 1 dwi-kompatibel, jadi aman dijalankan tanpa revert kode.

BEGIN;
ALTER TABLE credit_notes      DROP CONSTRAINT IF EXISTS fk_credit_notes_customer_tenant;
ALTER TABLE customer_deposits DROP CONSTRAINT IF EXISTS fk_customer_deposits_customer_tenant;
ALTER TABLE credit_notes      ALTER COLUMN customer_id TYPE varchar(255) USING customer_id::text;
ALTER TABLE customer_deposits ALTER COLUMN customer_id TYPE varchar(255) USING customer_id::text;
ALTER TABLE customers DROP CONSTRAINT IF EXISTS uq_customers_id_tenant;
COMMIT;
