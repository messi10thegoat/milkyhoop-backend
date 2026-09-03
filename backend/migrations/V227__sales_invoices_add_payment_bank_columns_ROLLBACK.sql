-- ROLLBACK V227: buang tiga kolom rekening tujuan cetak dari sales_invoices.
--
-- PERINGATAN: ini MENGHAPUS DATA. Setiap faktur yang sudah menyimpan instruksi
-- "bayar ke mana" kehilangan nilainya dan tidak bisa dipulihkan dari tabel lain
-- (nilainya snapshot teks, bukan FK ke bank_accounts). Ambil dump dulu.
--
-- Nol dampak akuntansi: kolom-kolom ini tak pernah masuk jurnal mana pun, jadi
-- rollback tidak mengubah satu baris journal_lines pun.

ALTER TABLE sales_invoices DROP COLUMN IF EXISTS payment_bank_name;
ALTER TABLE sales_invoices DROP COLUMN IF EXISTS payment_account_number;
ALTER TABLE sales_invoices DROP COLUMN IF EXISTS payment_account_holder;

DELETE FROM schema_migrations WHERE version = 'V227__sales_invoices_add_payment_bank_columns.sql';
