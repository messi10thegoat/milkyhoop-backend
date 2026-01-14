-- V024: Customers Table
-- Creates dedicated customers master data table with CRUD support
-- Mirrors vendors table structure for consistency

-- ============================================================================
-- CUSTOMERS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identity
    code VARCHAR(50),                         -- Optional customer code (e.g., CUST-001)
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
    payment_terms_days INTEGER DEFAULT 0,     -- Default jatuh tempo dalam hari (0 = tunai)
    credit_limit BIGINT,                      -- Batas kredit dalam Rupiah

    -- Metadata
    notes TEXT,
    is_active BOOLEAN DEFAULT true,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    -- Constraints
    CONSTRAINT uq_customers_tenant_name UNIQUE(tenant_id, name)
);

-- Partial unique index for code (only when code is not null)
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_tenant_code_unique
    ON customers(tenant_id, code)
    WHERE code IS NOT NULL;

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_customers_tenant ON customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_customers_tenant_name ON customers(tenant_id, name);
CREATE INDEX IF NOT EXISTS idx_customers_tenant_active ON customers(tenant_id, is_active)
    WHERE is_active = true;

-- Full-text search index for Indonesian
CREATE INDEX IF NOT EXISTS idx_customers_search ON customers
    USING gin(to_tsvector('indonesian', name || ' ' || COALESCE(contact_person, '')));

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_customers ON customers
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_customers_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION trigger_customers_updated_at();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE customers IS 'Master data pelanggan untuk faktur penjualan';
COMMENT ON COLUMN customers.code IS 'Kode pelanggan opsional (misal: CUST-001)';
COMMENT ON COLUMN customers.payment_terms_days IS 'Default jatuh tempo dalam hari dari tanggal faktur (0 = tunai)';
COMMENT ON COLUMN customers.credit_limit IS 'Batas kredit dalam Rupiah (null = tidak terbatas)';
COMMENT ON COLUMN customers.tax_id IS 'NPWP pelanggan untuk keperluan pajak';
COMMENT ON COLUMN customers.is_active IS 'False = pelanggan tidak aktif, tidak muncul di autocomplete';
