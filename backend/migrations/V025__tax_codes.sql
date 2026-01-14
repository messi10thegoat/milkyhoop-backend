-- V025: Tax Codes Table
-- Creates tax codes master data for PPN, PPh, and other taxes
-- Supports multiple tax rates per tenant

-- ============================================================================
-- TAX CODES TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS tax_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identity
    code VARCHAR(20) NOT NULL,                -- e.g., PPN-11, PPN-12, PPH-21
    name VARCHAR(100) NOT NULL,               -- e.g., PPN 11%, PPh 21

    -- Tax Configuration
    rate DECIMAL(5,2) NOT NULL,               -- Tax rate percentage (e.g., 11.00, 12.00)
    tax_type VARCHAR(20) NOT NULL,            -- ppn, pph21, pph23, pph4_2, custom
    is_inclusive BOOLEAN DEFAULT false,       -- Tax included in price by default

    -- Account Links (for journal entry)
    sales_tax_account VARCHAR(20),            -- CoA code for sales tax (e.g., 2-10400 PPN Keluaran)
    purchase_tax_account VARCHAR(20),         -- CoA code for purchase tax (e.g., 1-10500 PPN Masukan)

    -- Metadata
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,         -- Default tax for new items

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    -- Constraints
    CONSTRAINT uq_tax_codes_tenant_code UNIQUE(tenant_id, code),
    CONSTRAINT chk_tax_rate CHECK (rate >= 0 AND rate <= 100),
    CONSTRAINT chk_tax_type CHECK (tax_type IN ('ppn', 'pph21', 'pph23', 'pph4_2', 'custom', 'none'))
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_tax_codes_tenant ON tax_codes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tax_codes_tenant_type ON tax_codes(tenant_id, tax_type);
CREATE INDEX IF NOT EXISTS idx_tax_codes_tenant_active ON tax_codes(tenant_id, is_active)
    WHERE is_active = true;

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE tax_codes ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_tax_codes ON tax_codes
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_tax_codes_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tax_codes_updated_at
    BEFORE UPDATE ON tax_codes
    FOR EACH ROW EXECUTE FUNCTION trigger_tax_codes_updated_at();

-- ============================================================================
-- TRIGGER: Ensure only one default per tenant and tax_type
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_tax_codes_default()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_default = true THEN
        -- Clear other defaults for the same tenant and tax_type
        UPDATE tax_codes
        SET is_default = false, updated_at = NOW()
        WHERE tenant_id = NEW.tenant_id
          AND tax_type = NEW.tax_type
          AND id != NEW.id
          AND is_default = true;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tax_codes_default
    BEFORE INSERT OR UPDATE ON tax_codes
    FOR EACH ROW EXECUTE FUNCTION trigger_tax_codes_default();

-- ============================================================================
-- FUNCTION: Seed default tax codes for a tenant
-- ============================================================================
CREATE OR REPLACE FUNCTION seed_default_tax_codes(p_tenant_id TEXT)
RETURNS void AS $$
BEGIN
    -- PPN 11% (before April 2025)
    INSERT INTO tax_codes (tenant_id, code, name, rate, tax_type, sales_tax_account, purchase_tax_account, description)
    VALUES (p_tenant_id, 'PPN-11', 'PPN 11%', 11.00, 'ppn', '2-10400', '1-10500', 'Pajak Pertambahan Nilai 11%')
    ON CONFLICT (tenant_id, code) DO NOTHING;

    -- PPN 12% (from April 2025)
    INSERT INTO tax_codes (tenant_id, code, name, rate, tax_type, sales_tax_account, purchase_tax_account, description, is_default)
    VALUES (p_tenant_id, 'PPN-12', 'PPN 12%', 12.00, 'ppn', '2-10400', '1-10500', 'Pajak Pertambahan Nilai 12% (mulai 1 April 2025)', true)
    ON CONFLICT (tenant_id, code) DO NOTHING;

    -- PPN 0% (Bebas PPN)
    INSERT INTO tax_codes (tenant_id, code, name, rate, tax_type, description)
    VALUES (p_tenant_id, 'PPN-0', 'Bebas PPN', 0.00, 'ppn', 'Barang/jasa yang dibebaskan dari PPN')
    ON CONFLICT (tenant_id, code) DO NOTHING;

    -- No Tax
    INSERT INTO tax_codes (tenant_id, code, name, rate, tax_type, description)
    VALUES (p_tenant_id, 'NONE', 'Tanpa Pajak', 0.00, 'none', 'Tidak dikenakan pajak')
    ON CONFLICT (tenant_id, code) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE tax_codes IS 'Master data kode pajak untuk PPN, PPh, dan pajak lainnya';
COMMENT ON COLUMN tax_codes.code IS 'Kode pajak unik per tenant (misal: PPN-11, PPN-12)';
COMMENT ON COLUMN tax_codes.rate IS 'Persentase tarif pajak (misal: 11.00 untuk 11%)';
COMMENT ON COLUMN tax_codes.tax_type IS 'Jenis pajak: ppn, pph21, pph23, pph4_2, custom, none';
COMMENT ON COLUMN tax_codes.is_inclusive IS 'True jika pajak sudah termasuk dalam harga';
COMMENT ON COLUMN tax_codes.sales_tax_account IS 'Kode akun CoA untuk pajak penjualan';
COMMENT ON COLUMN tax_codes.purchase_tax_account IS 'Kode akun CoA untuk pajak pembelian';
COMMENT ON COLUMN tax_codes.is_default IS 'True jika ini pajak default untuk item baru';
