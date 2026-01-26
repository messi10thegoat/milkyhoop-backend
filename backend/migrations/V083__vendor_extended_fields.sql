-- ============================================================================
-- V083: Vendor Extended Fields
-- Adds account_number, bank details, and tax address fields
-- ============================================================================

-- Account number (vendor's internal reference number)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS account_number VARCHAR(50);

-- Bank details
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS bank_name VARCHAR(100);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS bank_account_number VARCHAR(50);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS bank_account_holder VARCHAR(255);

-- Tax address (separate from main address for e-Faktur)
ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_address TEXT;

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_city VARCHAR(100);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_province VARCHAR(100);

ALTER TABLE vendors
ADD COLUMN IF NOT EXISTS tax_postal_code VARCHAR(20);

-- Create index for account_number lookups
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_account_number
    ON vendors(tenant_id, account_number)
    WHERE account_number IS NOT NULL;

-- Documentation
COMMENT ON COLUMN vendors.account_number IS 'Vendor internal account/reference number';
COMMENT ON COLUMN vendors.bank_name IS 'Bank name for payments';
COMMENT ON COLUMN vendors.bank_account_number IS 'Bank account number';
COMMENT ON COLUMN vendors.bank_account_holder IS 'Bank account holder name';
COMMENT ON COLUMN vendors.tax_address IS 'Tax address street (for e-Faktur)';
COMMENT ON COLUMN vendors.tax_city IS 'Tax address city';
COMMENT ON COLUMN vendors.tax_province IS 'Tax address province';
COMMENT ON COLUMN vendors.tax_postal_code IS 'Tax address postal code';
