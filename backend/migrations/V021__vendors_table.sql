-- V021: Vendors Table
-- Creates dedicated vendors master data table with CRUD support
-- Replaces the pattern of storing vendor_name directly in bills

-- ============================================================================
-- VENDORS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identity
    code VARCHAR(50),                         -- Optional vendor code (e.g., PBF-001)
    name VARCHAR(255) NOT NULL,

    -- Contact
    contact_person VARCHAR(255),
    phone VARCHAR(50),
    email VARCHAR(255),

    -- Address
    address TEXT,
    city VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(20),

    -- Business Info
    tax_id VARCHAR(50),                       -- NPWP
    payment_terms_days INTEGER DEFAULT 30,    -- Default jatuh tempo dalam hari
    credit_limit BIGINT,                      -- Batas kredit dalam Rupiah

    -- Metadata
    notes TEXT,
    is_active BOOLEAN DEFAULT true,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    -- Constraints
    CONSTRAINT uq_vendors_tenant_name UNIQUE(tenant_id, name)
);

-- Partial unique index for code (only when code is not null)
CREATE UNIQUE INDEX IF NOT EXISTS idx_vendors_tenant_code_unique
    ON vendors(tenant_id, code)
    WHERE code IS NOT NULL;

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_vendors_tenant ON vendors(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_name ON vendors(tenant_id, name);
CREATE INDEX IF NOT EXISTS idx_vendors_tenant_active ON vendors(tenant_id, is_active)
    WHERE is_active = true;

-- Full-text search index for Indonesian
CREATE INDEX IF NOT EXISTS idx_vendors_search ON vendors
    USING gin(to_tsvector('indonesian', name || ' ' || COALESCE(contact_person, '')));

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE vendors ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_vendors ON vendors
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_vendors_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_vendors_updated_at
    BEFORE UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION trigger_vendors_updated_at();

-- ============================================================================
-- SEED DATA: Sample vendors for testing
-- ============================================================================
-- Note: Run this only in development/testing environment
-- Uncomment and replace 'your-tenant-id' with actual tenant_id

/*
INSERT INTO vendors (tenant_id, code, name, phone, address, city, payment_terms_days) VALUES
('your-tenant-id', 'PBF-001', 'PT. Century Franchisindo Utama', '021-7654321', 'Jl. Raya Bogor No. 123', 'Jakarta Timur', 30),
('your-tenant-id', 'PBF-002', 'PT. Kimia Farma Tbk', '021-4287800', 'Jl. Veteran No. 9', 'Jakarta Pusat', 30),
('your-tenant-id', 'PBF-003', 'PT. Kalbe Farma Tbk', '021-42873888', 'Jl. Let. Jend. Suprapto Kav. 4', 'Jakarta', 45),
('your-tenant-id', 'PBF-004', 'PT. Dexa Medica', '022-7312222', 'Jl. Jend. Bambang Utoyo No. 138', 'Palembang', 30),
('your-tenant-id', 'PBF-005', 'CV. Sumber Sehat', '024-8316666', 'Jl. Pandanaran No. 45', 'Semarang', 14);
*/

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE vendors IS 'Master data vendor/supplier untuk faktur pembelian';
COMMENT ON COLUMN vendors.code IS 'Kode vendor opsional (misal: PBF-001, CV-002)';
COMMENT ON COLUMN vendors.payment_terms_days IS 'Default jatuh tempo dalam hari dari tanggal faktur';
COMMENT ON COLUMN vendors.credit_limit IS 'Batas kredit dalam Rupiah (null = tidak terbatas)';
COMMENT ON COLUMN vendors.tax_id IS 'NPWP vendor untuk keperluan pajak';
COMMENT ON COLUMN vendors.is_active IS 'False = vendor tidak aktif, tidak muncul di autocomplete';
