-- V352 (PUTUSAN PEMILIK 30 Sep 2026, langsung di sesi BACKEND): "batas nota kredit = nilai faktur dikurangi nota
-- kredit lain; kelebihan jadi saldo kredit pelanggan". Bagian NK di atas sisa tagihan faktur dibukukan ke Uang Muka
-- Pelanggan + baris customer_deposits (prefiks KRD); tautan NK -> uang muka itu (untuk sisa uang muka yang
-- journal-derived dan pembatalan NK). Aditif, NULL.
ALTER TABLE credit_notes
    ADD COLUMN IF NOT EXISTS created_deposit_id uuid NULL REFERENCES customer_deposits(id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'credit_notes' AND column_name = 'created_deposit_id') THEN
        RAISE EXCEPTION 'V352: kolom credit_notes.created_deposit_id tidak mendarat';
    END IF;
END $$;
