-- V420 (13 Okt 2026, BACKEND; W-20261013-IDPAJAK, putusan MASTER opsi A) -- IDENTITAS PAJAK pembeli/penjual di MASTER.
-- Kolom ini dulu ada (FE menyebutnya "V125: Coretax fields") tapi HILANG bersama DB lama (droplet 24 Jul) dan tak masuk
-- resep pemulihan: form Pelanggan mengirim nitku/jenis_id/negara/nomor_dokumen, skema membuangnya diam, detail selalu "—";
-- builder faktur pajak (tax_invoices.py:147/243/332) merujuknya (dorman di balik penjaga modul e-Faktur 409).
-- Pola Coretax / Accurate (tab Pajak) / NetSuite ID localization: identitas di master, disnapshot ke faktur pajak saat terbit.
-- Aditif, nullable, NOL backfill (NULL = belum diisi; bawaan TIN/IDN tetap di builder). Tanpa efek uang.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS nitku varchar(22);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS jenis_id varchar(10);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS negara char(3);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS nomor_dokumen varchar(50);
ALTER TABLE vendors ADD COLUMN IF NOT EXISTS nitku varchar(22);
ALTER TABLE vendors ADD COLUMN IF NOT EXISTS negara char(3);

ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_nitku_22_digit;
ALTER TABLE customers ADD CONSTRAINT customers_nitku_22_digit CHECK (nitku IS NULL OR nitku ~ '^[0-9]{22}$');
ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_jenis_id_sah;
ALTER TABLE customers ADD CONSTRAINT customers_jenis_id_sah CHECK (jenis_id IS NULL OR jenis_id IN ('TIN', 'NIK', 'Passport', 'Other'));
ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_negara_iso3;
ALTER TABLE customers ADD CONSTRAINT customers_negara_iso3 CHECK (negara IS NULL OR negara ~ '^[A-Z]{3}$');
ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_nomor_dokumen_wajib;
ALTER TABLE customers ADD CONSTRAINT customers_nomor_dokumen_wajib
    CHECK (jenis_id IS NULL OR jenis_id NOT IN ('Passport', 'Other') OR nullif(btrim(nomor_dokumen), '') IS NOT NULL);
ALTER TABLE vendors DROP CONSTRAINT IF EXISTS vendors_nitku_22_digit;
ALTER TABLE vendors ADD CONSTRAINT vendors_nitku_22_digit CHECK (nitku IS NULL OR nitku ~ '^[0-9]{22}$');
ALTER TABLE vendors DROP CONSTRAINT IF EXISTS vendors_negara_iso3;
ALTER TABLE vendors ADD CONSTRAINT vendors_negara_iso3 CHECK (negara IS NULL OR negara ~ '^[A-Z]{3}$');

DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM information_schema.columns
     WHERE table_schema = 'public' AND is_nullable = 'YES'
       AND ((table_name = 'customers' AND column_name IN ('nitku', 'jenis_id', 'negara', 'nomor_dokumen'))
         OR (table_name = 'vendors' AND column_name IN ('nitku', 'negara')));
    IF n <> 6 THEN RAISE EXCEPTION 'V420: 6 kolom identitas pajak nullable tidak terpasang (%)', n; END IF;
    SELECT count(*) INTO n FROM pg_constraint
     WHERE conname IN ('customers_nitku_22_digit', 'customers_jenis_id_sah', 'customers_negara_iso3',
                       'customers_nomor_dokumen_wajib', 'vendors_nitku_22_digit', 'vendors_negara_iso3');
    IF n <> 6 THEN RAISE EXCEPTION 'V420: 6 CHECK identitas pajak tidak terpasang (%)', n; END IF;
END $$;
