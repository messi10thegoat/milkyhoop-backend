-- ============================================================================
-- V071: e-Faktur and PPh Withholding Fields
-- Adds fields for DJP Indonesia tax compliance
-- Supports e-Faktur Masukan/Keluaran and PPh bukti potong
-- ============================================================================

-- ============================================================================
-- STEP 1: Add e-Faktur and PPh fields to BILLS (Faktur Pembelian)
-- ============================================================================

-- e-Faktur Masukan fields
ALTER TABLE bills
ADD COLUMN IF NOT EXISTS efaktur_number VARCHAR(30),
ADD COLUMN IF NOT EXISTS efaktur_date DATE,
ADD COLUMN IF NOT EXISTS vendor_npwp VARCHAR(20),
ADD COLUMN IF NOT EXISTS vendor_is_pkp BOOLEAN DEFAULT false;

-- PPh Withholding fields
ALTER TABLE bills
ADD COLUMN IF NOT EXISTS pph_type VARCHAR(20),      -- PPH_23, PPH_4_2, PPH_21
ADD COLUMN IF NOT EXISTS pph_rate DECIMAL(5,2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS pph_amount BIGINT DEFAULT 0,
ADD COLUMN IF NOT EXISTS pph_dpp BIGINT DEFAULT 0,  -- DPP untuk PPh (bisa beda dari DPP PPN)
ADD COLUMN IF NOT EXISTS bukti_potong_number VARCHAR(50),
ADD COLUMN IF NOT EXISTS bukti_potong_date DATE;

-- Constraint for pph_type
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_bills_pph_type'
    ) THEN
        ALTER TABLE bills
            ADD CONSTRAINT chk_bills_pph_type
            CHECK (pph_type IS NULL OR pph_type IN ('PPH_21', 'PPH_23', 'PPH_4_2'));
    END IF;
END $$;

-- ============================================================================
-- STEP 2: Add e-Faktur fields to SALES_INVOICES (Faktur Penjualan)
-- ============================================================================

-- e-Faktur Keluaran fields
ALTER TABLE sales_invoices
ADD COLUMN IF NOT EXISTS efaktur_number VARCHAR(30),
ADD COLUMN IF NOT EXISTS efaktur_date DATE,
ADD COLUMN IF NOT EXISTS customer_npwp VARCHAR(20),
ADD COLUMN IF NOT EXISTS customer_nik VARCHAR(20),
ADD COLUMN IF NOT EXISTS efaktur_status VARCHAR(20) DEFAULT 'draft',
ADD COLUMN IF NOT EXISTS efaktur_approval_date DATE;

-- Update tax_rate to support 12% (if not already)
DO $$
BEGIN
    -- First check if constraint exists
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_sales_invoices_tax_rate'
    ) THEN
        ALTER TABLE sales_invoices DROP CONSTRAINT chk_sales_invoices_tax_rate;
    END IF;

    -- Add new constraint with 12%
    ALTER TABLE sales_invoices
        ADD CONSTRAINT chk_sales_invoices_tax_rate
        CHECK (tax_rate IS NULL OR tax_rate IN (0, 11, 12));
EXCEPTION
    WHEN others THEN
        -- Constraint may not exist, ignore
        NULL;
END $$;

-- Constraint for efaktur_status
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_sales_invoices_efaktur_status'
    ) THEN
        ALTER TABLE sales_invoices
            ADD CONSTRAINT chk_sales_invoices_efaktur_status
            CHECK (efaktur_status IS NULL OR efaktur_status IN ('draft', 'created', 'approved', 'cancelled'));
    END IF;
END $$;

-- ============================================================================
-- STEP 3: Create e-Faktur Sequences table (NSFP tracking)
-- ============================================================================
CREATE TABLE IF NOT EXISTS efaktur_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- NSFP range from DJP
    prefix VARCHAR(5) NOT NULL,       -- Kode cabang + tahun (e.g., '010.26')
    range_start BIGINT NOT NULL,      -- Nomor awal
    range_end BIGINT NOT NULL,        -- Nomor akhir
    current_number BIGINT NOT NULL,   -- Nomor terakhir digunakan

    -- Status
    is_active BOOLEAN DEFAULT true,
    allocated_date DATE,
    exhausted_at TIMESTAMPTZ,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT chk_efaktur_range CHECK (range_start <= range_end),
    CONSTRAINT chk_efaktur_current CHECK (current_number >= range_start - 1 AND current_number <= range_end)
);

-- ============================================================================
-- STEP 4: Create Bukti Potong PPh table
-- ============================================================================
CREATE TABLE IF NOT EXISTS bukti_potong (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Type
    pph_type VARCHAR(20) NOT NULL,    -- PPH_21, PPH_23, PPH_4_2

    -- Recipient (vendor)
    recipient_id UUID,                -- FK vendors
    recipient_name VARCHAR(255) NOT NULL,
    recipient_npwp VARCHAR(20),
    recipient_nik VARCHAR(20),
    recipient_address TEXT,

    -- Document
    bukti_potong_number VARCHAR(50) NOT NULL,
    bukti_potong_date DATE NOT NULL,
    tax_period VARCHAR(6) NOT NULL,   -- YYYYMM

    -- Jenis penghasilan (untuk PPh 23)
    income_type VARCHAR(100),         -- e.g., 'Jasa Teknik', 'Jasa Manajemen', 'Sewa'
    income_code VARCHAR(10),          -- Kode penghasilan DJP

    -- Amount
    dpp BIGINT NOT NULL,
    pph_rate DECIMAL(5,2) NOT NULL,
    pph_amount BIGINT NOT NULL,

    -- Source transaction
    source_type VARCHAR(20),          -- bill, expense
    source_id UUID,

    -- Status
    status VARCHAR(20) DEFAULT 'draft',  -- draft, final, reported

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT chk_bukti_potong_pph_type CHECK (pph_type IN ('PPH_21', 'PPH_23', 'PPH_4_2')),
    CONSTRAINT chk_bukti_potong_status CHECK (status IN ('draft', 'final', 'reported'))
);

-- ============================================================================
-- STEP 5: Create indexes
-- ============================================================================

-- Bills e-Faktur indexes
CREATE INDEX IF NOT EXISTS idx_bills_efaktur ON bills(tenant_id, efaktur_number) WHERE efaktur_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bills_pph_type ON bills(tenant_id, pph_type) WHERE pph_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bills_vendor_npwp ON bills(tenant_id, vendor_npwp) WHERE vendor_npwp IS NOT NULL;

-- Sales invoices e-Faktur indexes
CREATE INDEX IF NOT EXISTS idx_invoices_efaktur ON sales_invoices(tenant_id, efaktur_number) WHERE efaktur_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_invoices_efaktur_status ON sales_invoices(tenant_id, efaktur_status);
CREATE INDEX IF NOT EXISTS idx_invoices_customer_npwp ON sales_invoices(tenant_id, customer_npwp) WHERE customer_npwp IS NOT NULL;

-- e-Faktur sequences indexes
CREATE INDEX IF NOT EXISTS idx_efaktur_seq_tenant ON efaktur_sequences(tenant_id, is_active);

-- Bukti potong indexes
CREATE INDEX IF NOT EXISTS idx_bukti_potong_tenant ON bukti_potong(tenant_id);
CREATE INDEX IF NOT EXISTS idx_bukti_potong_period ON bukti_potong(tenant_id, tax_period);
CREATE INDEX IF NOT EXISTS idx_bukti_potong_type ON bukti_potong(tenant_id, pph_type);
CREATE INDEX IF NOT EXISTS idx_bukti_potong_recipient ON bukti_potong(tenant_id, recipient_id);
CREATE INDEX IF NOT EXISTS idx_bukti_potong_source ON bukti_potong(source_type, source_id);

-- ============================================================================
-- STEP 6: Enable RLS on new tables
-- ============================================================================
ALTER TABLE efaktur_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE bukti_potong ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_efaktur_sequences ON efaktur_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_bukti_potong ON bukti_potong
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- STEP 7: Trigger for bukti_potong updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION trigger_bukti_potong_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bukti_potong_updated_at
    BEFORE UPDATE ON bukti_potong
    FOR EACH ROW EXECUTE FUNCTION trigger_bukti_potong_updated_at();

-- ============================================================================
-- STEP 8: Function to generate e-Faktur number
-- ============================================================================
CREATE OR REPLACE FUNCTION generate_efaktur_number(p_tenant_id TEXT)
RETURNS VARCHAR(30) AS $$
DECLARE
    v_seq RECORD;
    v_number BIGINT;
    v_formatted VARCHAR(30);
BEGIN
    -- Get active sequence
    SELECT * INTO v_seq
    FROM efaktur_sequences
    WHERE tenant_id = p_tenant_id AND is_active = true
    ORDER BY created_at DESC
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'No active e-Faktur sequence found. Please allocate NSFP from DJP.';
    END IF;

    -- Check if exhausted
    IF v_seq.current_number >= v_seq.range_end THEN
        -- Deactivate current sequence
        UPDATE efaktur_sequences
        SET is_active = false, exhausted_at = NOW()
        WHERE id = v_seq.id;
        RAISE EXCEPTION 'e-Faktur sequence exhausted. Please allocate new NSFP from DJP.';
    END IF;

    -- Increment
    v_number := v_seq.current_number + 1;
    UPDATE efaktur_sequences SET current_number = v_number WHERE id = v_seq.id;

    -- Format: XXX-XXX.XX.XXXXXXXX (total 16 digits + separators)
    -- Example: 010-024.26.00000001
    v_formatted := v_seq.prefix || '.' || LPAD(v_number::TEXT, 8, '0');

    RETURN v_formatted;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- STEP 9: Function to generate Bukti Potong number
-- ============================================================================
CREATE OR REPLACE FUNCTION generate_bukti_potong_number(
    p_tenant_id TEXT,
    p_pph_type VARCHAR,
    p_tax_period VARCHAR
)
RETURNS VARCHAR(50) AS $$
DECLARE
    v_prefix VARCHAR(10);
    v_count INT;
    v_number VARCHAR(50);
BEGIN
    -- Determine prefix based on PPh type
    v_prefix := CASE p_pph_type
        WHEN 'PPH_21' THEN 'BP21'
        WHEN 'PPH_23' THEN 'BP23'
        WHEN 'PPH_4_2' THEN 'BP42'
        ELSE 'BP'
    END;

    -- Count existing bukti potong for this period
    SELECT COUNT(*) + 1 INTO v_count
    FROM bukti_potong
    WHERE tenant_id = p_tenant_id
      AND pph_type = p_pph_type
      AND tax_period = p_tax_period;

    -- Format: BP23-202601-0001
    v_number := v_prefix || '-' || p_tax_period || '-' || LPAD(v_count::TEXT, 4, '0');

    RETURN v_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- STEP 10: Function to calculate PPh from bill
-- ============================================================================
CREATE OR REPLACE FUNCTION calculate_bill_pph(
    p_dpp BIGINT,
    p_pph_type VARCHAR,
    p_vendor_type VARCHAR,
    p_has_npwp BOOLEAN
)
RETURNS TABLE (pph_rate DECIMAL, pph_amount BIGINT) AS $$
DECLARE
    v_rate DECIMAL;
BEGIN
    -- Get rate based on vendor type
    v_rate := get_vendor_pph_rate(p_vendor_type, p_has_npwp, p_pph_type);

    RETURN QUERY SELECT
        v_rate as pph_rate,
        ROUND(p_dpp * v_rate / 100)::BIGINT as pph_amount;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON COLUMN bills.efaktur_number IS 'Nomor Seri Faktur Pajak dari vendor PKP (16 digit)';
COMMENT ON COLUMN bills.efaktur_date IS 'Tanggal Faktur Pajak (bisa berbeda dari issue_date)';
COMMENT ON COLUMN bills.vendor_npwp IS 'NPWP vendor untuk e-Faktur Masukan';
COMMENT ON COLUMN bills.vendor_is_pkp IS 'True jika vendor adalah PKP (PPN bisa dikreditkan)';
COMMENT ON COLUMN bills.pph_type IS 'Jenis PPh: PPH_21, PPH_23, PPH_4_2';
COMMENT ON COLUMN bills.pph_rate IS 'Tarif PPh yang diterapkan';
COMMENT ON COLUMN bills.pph_amount IS 'Jumlah PPh yang dipotong';
COMMENT ON COLUMN bills.pph_dpp IS 'DPP untuk PPh (bisa berbeda dari DPP PPN)';
COMMENT ON COLUMN bills.bukti_potong_number IS 'Nomor bukti potong PPh';
COMMENT ON COLUMN bills.bukti_potong_date IS 'Tanggal bukti potong';

COMMENT ON COLUMN sales_invoices.efaktur_number IS 'Nomor Seri Faktur Pajak yang diterbitkan (16 digit)';
COMMENT ON COLUMN sales_invoices.efaktur_date IS 'Tanggal Faktur Pajak';
COMMENT ON COLUMN sales_invoices.customer_npwp IS 'NPWP pembeli untuk e-Faktur Keluaran';
COMMENT ON COLUMN sales_invoices.customer_nik IS 'NIK pembeli (jika NPWP tidak tersedia)';
COMMENT ON COLUMN sales_invoices.efaktur_status IS 'Status e-Faktur: draft, created, approved, cancelled';

COMMENT ON TABLE efaktur_sequences IS 'Tracking Nomor Seri Faktur Pajak (NSFP) dari DJP';
COMMENT ON COLUMN efaktur_sequences.prefix IS 'Kode cabang + tahun (e.g., 010.26 untuk cabang 010 tahun 2026)';
COMMENT ON COLUMN efaktur_sequences.range_start IS 'Nomor awal NSFP yang dialokasikan';
COMMENT ON COLUMN efaktur_sequences.range_end IS 'Nomor akhir NSFP yang dialokasikan';

COMMENT ON TABLE bukti_potong IS 'Bukti Potong PPh 21/23/4(2) untuk pelaporan SPT';
COMMENT ON COLUMN bukti_potong.tax_period IS 'Masa pajak dalam format YYYYMM';
COMMENT ON COLUMN bukti_potong.income_type IS 'Jenis penghasilan (untuk PPh 23)';
COMMENT ON COLUMN bukti_potong.income_code IS 'Kode penghasilan sesuai aturan DJP';

COMMENT ON FUNCTION generate_efaktur_number IS 'Generate e-Faktur number from allocated NSFP range';
COMMENT ON FUNCTION generate_bukti_potong_number IS 'Generate bukti potong number: BP23-YYYYMM-0001';
COMMENT ON FUNCTION calculate_bill_pph IS 'Calculate PPh amount based on vendor type and NPWP status';
