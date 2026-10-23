-- ROLLBACK V455: ditolak bila sudah ada tautan berjenis selain 'terima_dulu' (fakta pemasangan); selain itu kolom + CHECK dibuang.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM goods_receipt_bill_links WHERE kind <> 'terima_dulu') THEN
        RAISE EXCEPTION 'ROLLBACK V455 ditolak: sudah ada tautan tagih_dulu/legacy';
    END IF;
END $$;
ALTER TABLE goods_receipt_bill_links DROP CONSTRAINT IF EXISTS chk_grbl_kind;
ALTER TABLE goods_receipt_bill_links DROP COLUMN IF EXISTS kind;
