-- ============================================================================
-- V205__sales_invoice_revenue_status_allow_not_applicable.sql
--
-- BUG: POST /api/sales-invoices/{id}/void -> 500
--        new row for relation "sales_invoices" violates check constraint
--        "chk_si_revenue_status"  (sales_invoices.py:3576)
-- Artinya VOID invoice apa pun GAGAL (minimal delivery-mode / yang menyentuh
-- void path), sehingga koreksi/pembatalan faktur penjualan mustahil.
--
-- AKAR (bukan artefak recovery — bug desain asli V137):
--   V137__three_event_revenue_recognition.sql:61 membuat
--     CHECK (revenue_status IN ('deferred','partial','recognized'))
--   tanpa 'not_applicable'. Namun void_invoice (sales_invoices.py:3581, SATU-
--   SATUNYA penulis nilai itu) menjalankan:
--     UPDATE ... SET fulfillment_status='not_applicable',
--                    revenue_status   ='not_applicable'
--   Kolom SIBLING fulfillment_status MENGIZINKAN 'not_applicable'
--   (chk_si_fulfillment_status), revenue_status TIDAK -> asimetri.
--
-- ARBITER = KODE: void menulis 'not_applicable' dengan maksud jelas ("faktur
-- void, status tak berlaku lagi"), simetris dengan fulfillment_status. Yang
-- salah = constraint terlalu sempit, bukan kodenya. Fix = perlebar constraint
-- agar sejajar sibling-nya. Pola sama seperti V202 (bill_payments_v2 meniru
-- kembaran receive_payments).
-- ============================================================================

BEGIN;

ALTER TABLE sales_invoices DROP CONSTRAINT IF EXISTS chk_si_revenue_status;
ALTER TABLE sales_invoices ADD CONSTRAINT chk_si_revenue_status
    CHECK (revenue_status IN ('deferred','partial','recognized','not_applicable'));

-- Assertion fail-loud: nilai yang benar-benar ditulis void HARUS lolos.
DO $v205$
BEGIN
    PERFORM 1 WHERE 'not_applicable' IN ('deferred','partial','recognized','not_applicable');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'V205: not_applicable tidak masuk allowed-set revenue_status';
    END IF;
    -- pastikan constraint benar-benar menerima not_applicable (uji langsung)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid='sales_invoices'::regclass AND conname='chk_si_revenue_status'
           AND pg_get_constraintdef(oid) LIKE '%not_applicable%'
    ) THEN
        RAISE EXCEPTION 'V205: constraint belum memuat not_applicable';
    END IF;
    RAISE NOTICE 'V205 OK: revenue_status kini menerima not_applicable (simetris fulfillment_status)';
END $v205$;

COMMIT;
