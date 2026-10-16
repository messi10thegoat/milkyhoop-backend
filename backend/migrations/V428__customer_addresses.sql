-- V428 (16 Okt 2026, BACKEND; triase #4, kontrak backend/docs/KONTRAK-alamat-terstruktur-20261016.md, disetujui MASTER;
-- APPLY hanya sesudah GO pemilik) -- ALAMAT PELANGGAN TERSTRUKTUR, pola NetSuite addressbook / SAP BP address.
-- Banyak alamat per pelanggan; tepat satu bawaan tagih + satu bawaan kirim (indeks unik parsial = invarian di DB).
-- customers.alamat/city/province/postal_code TETAP (cermin bawaan tagih, ditulis services/alamat_pelanggan dalam tx sama).
-- Data lama: teks bebas -> addr1 APA ADANYA, tanpa tebakan (kota/kode pos tak dipecah dari teks); negara NULL tetap NULL.
-- Aditif; tak ada kolom lama yang diubah -> ROLLBACK tanpa kehilangan data lama. Tanpa efek uang.
CREATE TABLE IF NOT EXISTS customer_addresses (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    customer_id         uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    label               varchar(100),
    attention           varchar(255),
    addressee           varchar(255),
    phone               varchar(50),
    addr1               text,
    addr2               text,
    addr3               text,
    city                varchar(100),
    state               varchar(100),
    zip                 varchar(20),
    country             char(3),
    is_default_billing  boolean NOT NULL DEFAULT false,
    is_default_shipping boolean NOT NULL DEFAULT false,
    is_active           boolean NOT NULL DEFAULT true,
    sumber              varchar(20) NOT NULL DEFAULT 'pengguna',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text,
    updated_by          text,
    CONSTRAINT ca_isi CHECK (COALESCE(btrim(addr1), '') <> '' OR COALESCE(btrim(city), '') <> ''
                             OR COALESCE(btrim(state), '') <> '' OR COALESCE(btrim(zip), '') <> ''
                             OR NOT is_active),
    CONSTRAINT ca_negara CHECK (country IS NULL OR country ~ '^[A-Z]{3}$'),
    CONSTRAINT ca_bawaan_aktif CHECK (is_active OR NOT (is_default_billing OR is_default_shipping)),
    CONSTRAINT ca_sumber CHECK (sumber IN ('pengguna', 'migrasi_v428'))
);
CREATE INDEX IF NOT EXISTS idx_ca_pelanggan ON customer_addresses (tenant_id, customer_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_bawaan_tagih ON customer_addresses (customer_id) WHERE is_default_billing;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_bawaan_kirim ON customer_addresses (customer_id) WHERE is_default_shipping;

-- tenant alamat == tenant pelanggan (FK customers(id) tak memeriksa tenant; predikat baris -> ditegakkan DB)
CREATE OR REPLACE FUNCTION ca_tenant_sama() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = NEW.customer_id AND c.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'customer_addresses: tenant % bukan pemilik pelanggan %', NEW.tenant_id, NEW.customer_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_ca_tenant_sama ON customer_addresses;
CREATE TRIGGER trg_ca_tenant_sama BEFORE INSERT OR UPDATE OF tenant_id, customer_id ON customer_addresses
    FOR EACH ROW EXECUTE FUNCTION ca_tenant_sama();

-- asal alamat kirim di SO (NULL = alamat satu kali / SO lama); teks snapshot tetap sales_orders.shipping_address
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS shipping_address_id uuid
    REFERENCES customer_addresses(id) ON DELETE SET NULL;

-- Data lama -> satu baris bawaan tagih+kirim per pelanggan beralamat (idempoten: hanya yang belum punya baris)
INSERT INTO customer_addresses (tenant_id, customer_id, addr1, city, state, zip, country,
                                is_default_billing, is_default_shipping, sumber)
SELECT c.tenant_id, c.id,
       CASE WHEN COALESCE(btrim(c.alamat), '') <> '' THEN c.alamat END,
       CASE WHEN COALESCE(btrim(c.city), '') <> '' THEN c.city END,
       CASE WHEN COALESCE(btrim(c.province), '') <> '' THEN c.province END,
       CASE WHEN COALESCE(btrim(c.postal_code), '') <> '' THEN c.postal_code END,
       c.negara, true, true, 'migrasi_v428'
FROM customers c
WHERE (COALESCE(btrim(c.alamat), '') <> '' OR COALESCE(btrim(c.city), '') <> ''
       OR COALESCE(btrim(c.province), '') <> '' OR COALESCE(btrim(c.postal_code), '') <> '')
  AND NOT EXISTS (SELECT 1 FROM customer_addresses a WHERE a.customer_id = c.id);

-- SO lama: tautkan HANYA bila teks kirim sama dengan alamat pelanggan (pembanding btrim); selain itu NULL
UPDATE sales_orders so SET shipping_address_id = a.id
FROM customer_addresses a JOIN customers c ON c.id = a.customer_id AND c.tenant_id = a.tenant_id
WHERE a.customer_id = so.customer_id AND a.tenant_id = so.tenant_id AND a.sumber = 'migrasi_v428' AND a.is_default_billing
  AND so.shipping_address_id IS NULL AND COALESCE(btrim(so.shipping_address), '') <> ''
  AND btrim(so.shipping_address) = btrim(c.alamat);

DO $$
DECLARE n_dat int; n_ca int; n_so int;
BEGIN
    SELECT count(*) INTO n_dat FROM customers c WHERE COALESCE(btrim(c.alamat), '') <> '' OR COALESCE(btrim(c.city), '') <> ''
        OR COALESCE(btrim(c.province), '') <> '' OR COALESCE(btrim(c.postal_code), '') <> '';
    SELECT count(*) INTO n_ca FROM customer_addresses WHERE is_default_billing;
    SELECT count(*) INTO n_so FROM sales_orders WHERE shipping_address_id IS NOT NULL;
    IF n_ca < n_dat THEN
        RAISE EXCEPTION 'V428: % pelanggan beralamat tapi hanya % bawaan tagih', n_dat, n_ca;
    END IF;
    RAISE NOTICE 'V428: pelanggan beralamat=%, bawaan tagih=%, SO tertaut=%', n_dat, n_ca, n_so;
END $$;
