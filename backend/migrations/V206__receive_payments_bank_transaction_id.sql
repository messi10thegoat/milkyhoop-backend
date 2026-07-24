-- ============================================================================
-- V206__receive_payments_bank_transaction_id.sql
--
-- BUG: POST /api/sales-invoices/{id}/payments -> 500
--        column "bank_transaction_id" of relation "receive_payments" does not exist
--        (sales_invoices.py:2842, record_payment)
-- Artinya PELUNASAN faktur penjualan gagal -> siklus AR tak bisa ditutup.
--
-- AKAR (asimetri AR/AP, kelas sama dgn V202):
--   record_payment menjalankan
--     UPDATE receive_payments SET bank_transaction_id = $1 WHERE id = $2
--   untuk menautkan pelunasan ke bank_transaction mirror-nya. Kolom SIBLING
--   sisi AP `bill_payments_v2.bank_transaction_id` ADA (uuid); sisi AR
--   `receive_payments` TIDAK -> migrasi yang menambah link ini hanya mengenai
--   AP, melewatkan AR (kebalikan arah V202).
--   Satu-satunya penulis: sales_invoices.py:2843. Belum ada pembaca SELECT,
--   tapi ini kolom traceability yang sibling-nya pelihara; menambah kolom
--   memulihkan simetri dan memuaskan penulis (menghapus UPDATE = memutus link
--   yang sibling AP simpan). Tipe ditiru PERSIS dari bill_payments_v2 (uuid).
--
-- CATATAN alat: audit_insert_schema_drift.py melewatkan ini karena hanya
-- memindai INSERT, sedangkan drift ini di UPDATE. Alat akan diperluas ke UPDATE
-- (perbaikan tooling terpisah, bukan migrasi).
-- ============================================================================

BEGIN;

ALTER TABLE receive_payments ADD COLUMN IF NOT EXISTS bank_transaction_id UUID;
COMMENT ON COLUMN receive_payments.bank_transaction_id IS
    'Link ke bank_transaction mirror (BankSync). Padanan bill_payments_v2.bank_transaction_id sisi AP.';

DO $v206$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='receive_payments' AND column_name='bank_transaction_id') THEN
        RAISE EXCEPTION 'V206: kolom belum terbentuk';
    END IF;
    RAISE NOTICE 'V206 OK: receive_payments.bank_transaction_id (simetris bill_payments_v2)';
END $v206$;

COMMIT;
