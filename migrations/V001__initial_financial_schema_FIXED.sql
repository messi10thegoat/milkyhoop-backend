-- ============================================
-- MilkyHoop 4.0 - Financial Schema Migration (FIXED)
-- Version: V001__initial_financial_schema_FIXED.sql
-- Date: 2025-10-29
-- SAK EMKM Compliant - Indonesian UMKM Standard
-- FIX: Changed UUID to TEXT to match existing schema
-- ============================================

-- ============================================
-- 1. CORE TRANSACTION TABLE (Immutable Event Log)
-- ============================================
CREATE TABLE IF NOT EXISTS transaksi_harian (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    tenant_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    actor_role VARCHAR(50) NOT NULL, -- 'owner', 'bendahara', 'staf_toko', 'kurir'
    timestamp BIGINT NOT NULL, -- Unix timestamp (milliseconds)
    
    -- Transaction type discriminator
    jenis_transaksi VARCHAR(50) NOT NULL, -- 'penjualan', 'pembelian', 'beban'
    
    -- Transaction payload (JSONB for flexibility)
    payload JSONB NOT NULL,
    
    -- Audit fields
    raw_nlu BYTEA,
    raw_text TEXT NOT NULL,
    metadata JSONB,
    receipt_url TEXT,
    receipt_checksum VARCHAR(64),
    idempotency_key VARCHAR(255) UNIQUE,
    
    -- Approval workflow
    status VARCHAR(50) DEFAULT 'draft', -- 'draft', 'pending', 'approved', 'rejected'
    approved_by TEXT,
    approved_at BIGINT,
    
    -- Bank account tracking
    rekening_id VARCHAR(100),
    rekening_type VARCHAR(50), -- 'pribadi', 'bisnis', 'campur'
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE,
    CONSTRAINT fk_created_by FOREIGN KEY (created_by) REFERENCES "User"(id) ON DELETE RESTRICT,
    CONSTRAINT fk_approved_by FOREIGN KEY (approved_by) REFERENCES "User"(id) ON DELETE RESTRICT,
    CONSTRAINT chk_jenis_transaksi CHECK (jenis_transaksi IN ('penjualan', 'pembelian', 'beban')),
    CONSTRAINT chk_status CHECK (status IN ('draft', 'pending', 'approved', 'rejected')),
    CONSTRAINT chk_rekening_type CHECK (rekening_type IN ('pribadi', 'bisnis', 'campur'))
);

-- Indexes for performance
CREATE INDEX idx_transaksi_tenant ON transaksi_harian(tenant_id);
CREATE INDEX idx_transaksi_timestamp ON transaksi_harian(timestamp);
CREATE INDEX idx_transaksi_jenis ON transaksi_harian(jenis_transaksi);
CREATE INDEX idx_transaksi_status ON transaksi_harian(status);
CREATE INDEX idx_transaksi_created_by ON transaksi_harian(created_by);
CREATE INDEX idx_transaksi_idempotency ON transaksi_harian(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Composite index for common queries
CREATE INDEX idx_transaksi_tenant_timestamp ON transaksi_harian(tenant_id, timestamp DESC);
CREATE INDEX idx_transaksi_tenant_status ON transaksi_harian(tenant_id, status);

-- JSONB GIN index for payload queries
CREATE INDEX idx_transaksi_payload_gin ON transaksi_harian USING GIN (payload);

-- Comment
COMMENT ON TABLE transaksi_harian IS 'Immutable event log for all financial transactions (SAK EMKM compliant)';

-- ============================================
-- 2. OUTBOX TABLE (Event Sourcing Pattern)
-- ============================================
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    transaksi_id TEXT NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP,
    retry_count INT DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT fk_transaksi FOREIGN KEY (transaksi_id) REFERENCES transaksi_harian(id) ON DELETE CASCADE
);

-- Indexes for projection worker
CREATE INDEX idx_outbox_processed ON outbox(processed, created_at) WHERE processed = FALSE;
CREATE INDEX idx_outbox_transaksi ON outbox(transaksi_id);
CREATE INDEX idx_outbox_event_type ON outbox(event_type);

COMMENT ON TABLE outbox IS 'Event sourcing outbox for reliable event publishing to projection worker';

-- ============================================
-- 3. TAX INFO TABLE (Real-time Omzet Tracking)
-- ============================================
CREATE TABLE IF NOT EXISTS tax_info (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    tenant_id TEXT NOT NULL,
    periode VARCHAR(7) NOT NULL, -- 'YYYY-MM' format (e.g., '2025-10')
    
    -- Omzet tracking
    omzet_bulan_ini BIGINT DEFAULT 0,
    omzet_tahun_berjalan BIGINT DEFAULT 0,
    
    -- Threshold monitoring
    exceeds_500juta BOOLEAN DEFAULT FALSE,
    exceeds_4_8milyar BOOLEAN DEFAULT FALSE,
    
    -- Tax calculation
    pph_final_terutang BIGINT DEFAULT 0, -- 0.5% of omzet if exceeds 500jt
    pph_final_terbayar BIGINT DEFAULT 0,
    
    -- Metadata
    is_pkp BOOLEAN DEFAULT FALSE, -- Pengusaha Kena Pajak
    status_wp VARCHAR(50), -- 'orang_pribadi', 'cv', 'pt'
    tahun_terdaftar INT,
    
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT fk_tenant_tax FOREIGN KEY (tenant_id) REFERENCES "Tenant"(id) ON DELETE CASCADE,
    CONSTRAINT uq_tenant_periode UNIQUE(tenant_id, periode),
    CONSTRAINT chk_status_wp CHECK (status_wp IN ('orang_pribadi', 'cv', 'pt', 'ud', 'firma'))
);

-- Indexes
CREATE INDEX idx_tax_info_tenant ON tax_info(tenant_id);
CREATE INDEX idx_tax_info_periode ON tax_info(periode);
CREATE INDEX idx_tax_info_exceeds ON tax_info(tenant_id, exceeds_500juta, exceeds_4_8milyar);

COMMENT ON TABLE tax_info IS 'Real-time tax threshold monitoring for Indonesian PPh Final 0.5%';

-- ============================================
-- 4. MATERIALIZED VIEW: Laporan Laba Rugi
-- ============================================
CREATE MATERIALIZED VIEW IF NOT EXISTS laporan_laba_rugi AS
SELECT 
    tenant_id,
    DATE_TRUNC('month', TO_TIMESTAMP(timestamp/1000)) AS periode,
    
    -- Pendapatan (from penjualan)
    SUM(CASE 
        WHEN jenis_transaksi = 'penjualan' AND status = 'approved'
        THEN (payload->>'total_nominal')::BIGINT 
        ELSE 0 
    END) AS pendapatan,
    
    -- HPP / Pembelian (from pembelian)
    SUM(CASE 
        WHEN jenis_transaksi = 'pembelian' AND status = 'approved'
        THEN (payload->>'total_nominal')::BIGINT 
        ELSE 0 
    END) AS pembelian,
    
    -- Beban (from beban)
    SUM(CASE 
        WHEN jenis_transaksi = 'beban' AND status = 'approved'
        THEN (payload->>'nominal')::BIGINT 
        ELSE 0 
    END) AS beban,
    
    -- Laba Kotor
    SUM(CASE 
        WHEN jenis_transaksi = 'penjualan' AND status = 'approved'
        THEN (payload->>'total_nominal')::BIGINT 
        ELSE 0 
    END) - SUM(CASE 
        WHEN jenis_transaksi = 'pembelian' AND status = 'approved'
        THEN (payload->>'total_nominal')::BIGINT 
        ELSE 0 
    END) AS laba_kotor,
    
    -- Laba Bersih
    SUM(CASE 
        WHEN jenis_transaksi = 'penjualan' AND status = 'approved'
        THEN (payload->>'total_nominal')::BIGINT 
        ELSE 0 
    END) - SUM(CASE 
        WHEN jenis_transaksi = 'pembelian' AND status = 'approved'
        THEN (payload->>'total_nominal')::BIGINT 
        ELSE 0 
    END) - SUM(CASE 
        WHEN jenis_transaksi = 'beban' AND status = 'approved'
        THEN (payload->>'nominal')::BIGINT 
        ELSE 0 
    END) AS laba_bersih,
    
    -- Transaction counts
    COUNT(*) FILTER (WHERE jenis_transaksi = 'penjualan' AND status = 'approved') AS jumlah_penjualan,
    COUNT(*) FILTER (WHERE jenis_transaksi = 'pembelian' AND status = 'approved') AS jumlah_pembelian,
    COUNT(*) FILTER (WHERE jenis_transaksi = 'beban' AND status = 'approved') AS jumlah_beban,
    
    -- Last updated
    MAX(updated_at) AS last_updated
FROM transaksi_harian
WHERE status = 'approved'
GROUP BY tenant_id, DATE_TRUNC('month', TO_TIMESTAMP(timestamp/1000));

-- Indexes on materialized view
CREATE UNIQUE INDEX idx_laporan_laba_rugi_tenant_periode ON laporan_laba_rugi(tenant_id, periode);
CREATE INDEX idx_laporan_laba_rugi_periode ON laporan_laba_rugi(periode);

COMMENT ON MATERIALIZED VIEW laporan_laba_rugi IS 'SAK EMKM Laporan Laba Rugi - Pre-computed monthly profit/loss statement';

-- ============================================
-- 5. MATERIALIZED VIEW: Laporan Posisi Keuangan
-- ============================================
CREATE MATERIALIZED VIEW IF NOT EXISTS laporan_posisi_keuangan AS
SELECT 
    tenant_id,
    DATE_TRUNC('month', TO_TIMESTAMP(timestamp/1000)) AS periode,
    
    -- Piutang (accounts receivable)
    SUM(CASE 
        WHEN jenis_transaksi = 'penjualan' 
        AND status = 'approved'
        AND (payload->>'payment_status') != 'lunas'
        THEN ((payload->>'total_nominal')::BIGINT - COALESCE((payload->>'amount_paid')::BIGINT, 0))
        ELSE 0 
    END) AS piutang,
    
    -- Utang (accounts payable)
    SUM(CASE 
        WHEN jenis_transaksi = 'pembelian' 
        AND status = 'approved'
        AND (payload->>'payment_status') != 'lunas'
        THEN ((payload->>'total_nominal')::BIGINT - COALESCE((payload->>'amount_paid')::BIGINT, 0))
        ELSE 0 
    END) AS utang,
    
    -- Kas/Bank (cumulative cash flow)
    SUM(CASE 
        WHEN jenis_transaksi = 'penjualan' AND status = 'approved'
        THEN COALESCE((payload->>'amount_paid')::BIGINT, (payload->>'total_nominal')::BIGINT)
        WHEN jenis_transaksi = 'pembelian' AND status = 'approved'
        THEN -COALESCE((payload->>'amount_paid')::BIGINT, (payload->>'total_nominal')::BIGINT)
        WHEN jenis_transaksi = 'beban' AND status = 'approved'
        THEN -(payload->>'nominal')::BIGINT
        ELSE 0 
    END) AS kas_bank,
    
    MAX(updated_at) AS last_updated
FROM transaksi_harian
WHERE status = 'approved'
GROUP BY tenant_id, DATE_TRUNC('month', TO_TIMESTAMP(timestamp/1000));

-- Indexes
CREATE UNIQUE INDEX idx_laporan_posisi_tenant_periode ON laporan_posisi_keuangan(tenant_id, periode);

COMMENT ON MATERIALIZED VIEW laporan_posisi_keuangan IS 'SAK EMKM Laporan Posisi Keuangan - Balance sheet with assets and liabilities';

-- ============================================
-- 6. TRIGGER: Auto-update updated_at
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_transaksi_harian_updated_at
    BEFORE UPDATE ON transaksi_harian
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tax_info_updated_at
    BEFORE UPDATE ON tax_info
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 7. ROW LEVEL SECURITY (Multi-tenant Isolation)
-- ============================================
ALTER TABLE transaksi_harian ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE tax_info ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only access their own tenant data
CREATE POLICY tenant_isolation_transaksi ON transaksi_harian
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_outbox ON outbox
    FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM transaksi_harian 
            WHERE transaksi_harian.id = outbox.transaksi_id 
            AND transaksi_harian.tenant_id = current_setting('app.current_tenant_id', TRUE)
        )
    );

CREATE POLICY tenant_isolation_tax ON tax_info
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

-- ============================================
-- END OF MIGRATION
-- ============================================
