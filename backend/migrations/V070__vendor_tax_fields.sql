-- ============================================================================
-- V070: Vendor Tax Fields
-- Adds tax-related fields for PPh 23 withholding and PPN (PKP status)
-- Critical for Indonesian tax compliance
-- ============================================================================

-- ============================================================================
-- STEP 1: Add tax-related columns to vendors table
-- ============================================================================

-- Vendor type (BADAN/ORANG_PRIBADI) - determines PPh rate
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS vendor_type VARCHAR(20) DEFAULT 'BADAN';

-- NIK (for ORANG_PRIBADI vendors)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS nik VARCHAR(20);

-- PKP status (Pengusaha Kena Pajak) - determines if PPN can be credited
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS is_pkp BOOLEAN DEFAULT false;

-- Default tax settings
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS default_tax_code VARCHAR(20),
ADD COLUMN IF NOT EXISTS default_pph_type VARCHAR(20),
ADD COLUMN IF NOT EXISTS default_pph_rate DECIMAL(5,2) DEFAULT 0;

-- Company/business name (separate from contact name)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS company_name VARCHAR(255);

-- Display name (for documents)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);

-- Additional contact info
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS mobile_phone VARCHAR(50),
ADD COLUMN IF NOT EXISTS website VARCHAR(255);

-- Multi-currency support
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'IDR';

-- Opening balance (for migration)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS opening_balance BIGINT DEFAULT 0,
ADD COLUMN IF NOT EXISTS opening_balance_date DATE;

-- ============================================================================
-- STEP 2: Add constraints
-- ============================================================================

-- Constraint for vendor_type
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_vendors_vendor_type'
    ) THEN
        ALTER TABLE vendors
            ADD CONSTRAINT chk_vendors_vendor_type
            CHECK (vendor_type IN ('BADAN', 'ORANG_PRIBADI', 'LUAR_NEGERI'));
    END IF;
END $$;

-- Constraint for default_pph_type
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_vendors_pph_type'
    ) THEN
        ALTER TABLE vendors
            ADD CONSTRAINT chk_vendors_pph_type
            CHECK (default_pph_type IS NULL OR default_pph_type IN ('PPH_21', 'PPH_23', 'PPH_4_2'));
    END IF;
END $$;

-- ============================================================================
-- STEP 3: Create vendor_addresses table (multiple addresses)
-- ============================================================================
CREATE TABLE IF NOT EXISTS vendor_addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,

    address_type VARCHAR(20) NOT NULL,  -- 'billing', 'shipping'
    label VARCHAR(100),                  -- e.g., "Kantor Pusat", "Gudang"
    street TEXT,
    city VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100) DEFAULT 'Indonesia',

    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_vendor_address_type CHECK (address_type IN ('billing', 'shipping'))
);

-- ============================================================================
-- STEP 4: Create vendor_contacts table (multiple contacts)
-- ============================================================================
CREATE TABLE IF NOT EXISTS vendor_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,

    name VARCHAR(255) NOT NULL,
    position VARCHAR(100),
    email VARCHAR(255),
    phone VARCHAR(50),
    mobile VARCHAR(50),

    is_primary BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- STEP 5: Create indexes
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_vendor_addresses_vendor ON vendor_addresses(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendor_contacts_vendor ON vendor_contacts(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_type ON vendors(tenant_id, vendor_type);
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_pkp ON vendors(tenant_id, is_pkp) WHERE is_pkp = true;
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_currency ON vendors(tenant_id, currency);

-- ============================================================================
-- STEP 6: Enable RLS on new tables
-- ============================================================================
ALTER TABLE vendor_addresses ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_contacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_vendor_addresses ON vendor_addresses
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_vendor_contacts ON vendor_contacts
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- STEP 7: Migrate existing data
-- ============================================================================

-- Set default values for existing vendors
UPDATE vendors SET vendor_type = 'BADAN' WHERE vendor_type IS NULL;
UPDATE vendors SET is_pkp = false WHERE is_pkp IS NULL;
UPDATE vendors SET currency = 'IDR' WHERE currency IS NULL;
UPDATE vendors SET opening_balance = 0 WHERE opening_balance IS NULL;

-- Migrate existing address to vendor_addresses (billing)
INSERT INTO vendor_addresses (tenant_id, vendor_id, address_type, street, city, province, postal_code, is_default)
SELECT
    tenant_id,
    id as vendor_id,
    'billing' as address_type,
    address as street,
    city,
    province,
    postal_code,
    true as is_default
FROM vendors
WHERE address IS NOT NULL OR city IS NOT NULL
ON CONFLICT DO NOTHING;

-- Migrate existing contact_person to vendor_contacts
INSERT INTO vendor_contacts (tenant_id, vendor_id, name, phone, email, is_primary)
SELECT
    tenant_id,
    id as vendor_id,
    contact_person as name,
    phone,
    email,
    true as is_primary
FROM vendors
WHERE contact_person IS NOT NULL
ON CONFLICT DO NOTHING;

-- ============================================================================
-- STEP 8: Function to calculate PPh rate based on vendor type
-- ============================================================================
CREATE OR REPLACE FUNCTION get_vendor_pph_rate(
    p_vendor_type VARCHAR,
    p_has_npwp BOOLEAN,
    p_pph_type VARCHAR
) RETURNS DECIMAL AS $$
BEGIN
    -- PPh 23 rates (jasa)
    IF p_pph_type = 'PPH_23' THEN
        IF p_vendor_type = 'BADAN' THEN
            RETURN 2.0;  -- 2% for badan usaha
        ELSIF p_vendor_type = 'ORANG_PRIBADI' THEN
            IF p_has_npwp THEN
                RETURN 2.0;  -- 2% with NPWP
            ELSE
                RETURN 4.0;  -- 4% without NPWP (200% tarif normal)
            END IF;
        ELSIF p_vendor_type = 'LUAR_NEGERI' THEN
            RETURN 20.0; -- 20% for foreign vendors (PPh 26)
        END IF;

    -- PPh 4(2) - Final (sewa tanah/bangunan)
    ELSIF p_pph_type = 'PPH_4_2' THEN
        RETURN 10.0;  -- 10% final

    -- PPh 21 - Simplified (should use TER in practice)
    ELSIF p_pph_type = 'PPH_21' THEN
        IF p_has_npwp THEN
            RETURN 5.0;  -- Simplified progressive rate
        ELSE
            RETURN 6.0;  -- 120% without NPWP
        END IF;
    END IF;

    RETURN 0;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- STEP 9: Function to validate NPWP format
-- ============================================================================
CREATE OR REPLACE FUNCTION validate_npwp_format(p_npwp VARCHAR)
RETURNS BOOLEAN AS $$
BEGIN
    -- Remove dots and dashes
    p_npwp := REGEXP_REPLACE(p_npwp, '[.-]', '', 'g');

    -- NPWP Badan: 15 digits
    -- NPWP Pribadi (NIK): 16 digits
    IF LENGTH(p_npwp) = 15 OR LENGTH(p_npwp) = 16 THEN
        -- Check all digits
        IF p_npwp ~ '^[0-9]+$' THEN
            RETURN true;
        END IF;
    END IF;

    RETURN false;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- STEP 10: Trigger for auto-set display_name
-- ============================================================================
CREATE OR REPLACE FUNCTION trg_vendors_set_display_name()
RETURNS TRIGGER AS $$
BEGIN
    -- Set display_name from company_name or name if not provided
    IF NEW.display_name IS NULL OR NEW.display_name = '' THEN
        NEW.display_name := COALESCE(NEW.company_name, NEW.name);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vendors_display_name ON vendors;
CREATE TRIGGER trg_vendors_display_name
    BEFORE INSERT OR UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION trg_vendors_set_display_name();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON COLUMN vendors.vendor_type IS 'Type: BADAN (PT/CV), ORANG_PRIBADI (individual), LUAR_NEGERI (foreign)';
COMMENT ON COLUMN vendors.nik IS 'NIK 16 digit untuk vendor ORANG_PRIBADI (NIK = NPWP sejak 2024)';
COMMENT ON COLUMN vendors.is_pkp IS 'PKP status - only PPN from PKP vendors can be credited';
COMMENT ON COLUMN vendors.default_tax_code IS 'Default tax code for transactions (PPN_11, PPN_12, etc)';
COMMENT ON COLUMN vendors.default_pph_type IS 'Default PPh type: PPH_21, PPH_23, PPH_4_2';
COMMENT ON COLUMN vendors.company_name IS 'Legal company name (PT/CV) - separate from contact name';
COMMENT ON COLUMN vendors.display_name IS 'Name shown on documents and invoices';
COMMENT ON COLUMN vendors.currency IS 'Default currency for this vendor (IDR, USD, etc)';
COMMENT ON COLUMN vendors.opening_balance IS 'Opening balance (hutang) saat migrasi dalam Rupiah';

COMMENT ON TABLE vendor_addresses IS 'Multiple addresses per vendor (billing/shipping)';
COMMENT ON COLUMN vendor_addresses.address_type IS 'Type: billing (penagihan), shipping (pengiriman)';

COMMENT ON TABLE vendor_contacts IS 'Multiple contact persons per vendor';
COMMENT ON COLUMN vendor_contacts.is_primary IS 'Primary contact for communications';

COMMENT ON FUNCTION get_vendor_pph_rate IS 'Calculate PPh rate based on vendor type and NPWP status';
COMMENT ON FUNCTION validate_npwp_format IS 'Validate NPWP format (15 or 16 digits)';
