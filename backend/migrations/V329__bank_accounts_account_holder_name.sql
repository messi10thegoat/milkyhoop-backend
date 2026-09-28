-- V329 (28 Sep 2026, pemilik lewat MASTER): "Nama pemilik rekening" di Kas & Bank.
-- Faktur/proforma/WA dulu mencetak "a.n. <nama akun internal>" (mis. "BCA Pemasukan") karena snapshot
-- payment_account_holder diisi dengan account_name. Kolom ini = nama pemilik rekening sebenarnya, diisi
-- PEMILIK lewat form Kas & Bank. NULL = belum diisi -> cetak tanpa "a.n." (tak mengarang). TANPA backfill.
ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS account_holder_name TEXT NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'bank_accounts' AND column_name = 'account_holder_name' AND is_nullable = 'YES') THEN
    RAISE EXCEPTION 'V329: bank_accounts.account_holder_name tak terpasang';
  END IF;
END $$;
