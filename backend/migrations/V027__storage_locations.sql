-- V027: Storage Locations (Lokasi Penyimpanan)
-- Creates storage locations for inventory management within a single outlet
-- Note: Lokasi = zona/bin dalam 1 outlet, BUKAN multi-outlet

-- ============================================================================
-- STORAGE LOCATIONS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS storage_locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Identity
    code VARCHAR(50) NOT NULL,               -- e.g., RAK-A1, GUDANG-01
    name VARCHAR(255) NOT NULL,              -- e.g., Rak Obat Bebas A1

    -- Hierarchy (optional)
    parent_id UUID REFERENCES storage_locations(id),
    location_type VARCHAR(20) DEFAULT 'bin', -- warehouse, zone, rack, bin

    -- Physical attributes
    address TEXT,                            -- For warehouse-level locations
    capacity_info TEXT,                      -- Free-form capacity description
    temperature_range VARCHAR(50),           -- e.g., "2-8°C" for cold storage

    -- Metadata
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,        -- Default location for new items

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    -- Constraints
    CONSTRAINT uq_storage_locations_tenant_code UNIQUE(tenant_id, code),
    CONSTRAINT chk_location_type CHECK (location_type IN ('warehouse', 'zone', 'rack', 'bin', 'shelf', 'other'))
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_storage_locations_tenant ON storage_locations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_storage_locations_tenant_active ON storage_locations(tenant_id, is_active)
    WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_storage_locations_parent ON storage_locations(parent_id);
CREATE INDEX IF NOT EXISTS idx_storage_locations_type ON storage_locations(tenant_id, location_type);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE storage_locations ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_storage_locations ON storage_locations
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_storage_locations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_storage_locations_updated_at
    BEFORE UPDATE ON storage_locations
    FOR EACH ROW EXECUTE FUNCTION trigger_storage_locations_updated_at();

-- ============================================================================
-- TRIGGER: Ensure only one default per tenant
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_storage_locations_default()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_default = true THEN
        UPDATE storage_locations
        SET is_default = false, updated_at = NOW()
        WHERE tenant_id = NEW.tenant_id
          AND id != NEW.id
          AND is_default = true;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_storage_locations_default
    BEFORE INSERT OR UPDATE ON storage_locations
    FOR EACH ROW EXECUTE FUNCTION trigger_storage_locations_default();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE storage_locations IS 'Lokasi penyimpanan/gudang dalam 1 outlet (bukan multi-outlet)';
COMMENT ON COLUMN storage_locations.code IS 'Kode lokasi unik per tenant (misal: RAK-A1, GUDANG-01)';
COMMENT ON COLUMN storage_locations.location_type IS 'Tipe: warehouse, zone, rack, bin, shelf, other';
COMMENT ON COLUMN storage_locations.parent_id IS 'Hierarki lokasi (misal: bin dalam rack, rack dalam zone)';
COMMENT ON COLUMN storage_locations.is_default IS 'Lokasi default untuk item baru';
