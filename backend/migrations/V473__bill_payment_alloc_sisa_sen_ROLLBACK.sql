-- V473 ROLLBACK: kembalikan remaining_before / remaining_after ke BIGINT. MENOLAK bila ada nilai bersen (membulatkan = mengubah
-- catatan uang diam-diam, Law 9) -- jalankan hanya bila kode yang menulis sen sudah dikembalikan dan tak ada baris bersen.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM bill_payment_allocations
               WHERE remaining_before <> trunc(remaining_before) OR remaining_after <> trunc(remaining_after)) THEN
        RAISE EXCEPTION 'V473 ROLLBACK ditolak: ada sisa bersen di bill_payment_allocations';
    END IF;
END $$;
ALTER TABLE bill_payment_allocations
    ALTER COLUMN remaining_before TYPE bigint USING remaining_before::bigint,
    ALTER COLUMN remaining_after TYPE bigint USING remaining_after::bigint;
