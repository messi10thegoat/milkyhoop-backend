-- Pembalik V467. Gagal keras bila sudah ada penawaran berongkir (membuang kolom = mengubah total yang tercetak).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'quotes' AND column_name = 'shipping_amount')
       AND EXISTS (SELECT 1 FROM quotes WHERE shipping_amount <> 0 OR shipping_tax_code_id IS NOT NULL) THEN
        RAISE EXCEPTION 'V467 ROLLBACK ditolak: ada penawaran berongkir';
    END IF;
END $$;
ALTER TABLE quotes DROP CONSTRAINT IF EXISTS chk_quotes_shipping_nonneg;
ALTER TABLE quotes
    DROP COLUMN IF EXISTS shipping_dpp,
    DROP COLUMN IF EXISTS shipping_tax_amount,
    DROP COLUMN IF EXISTS shipping_tax_rate,
    DROP COLUMN IF EXISTS shipping_tax_code_id,
    DROP COLUMN IF EXISTS shipping_amount;
