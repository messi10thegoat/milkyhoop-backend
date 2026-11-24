-- V509: STOK OPNAME bernomor (putusan pemilik LANGSUNG 24 Nov 2026; ikut Accurate: hitung fisik per gudang, gudang TERKUNCI selama hitung,
-- selisih -> Penyesuaian Stok recount + jurnal lewat jalur SA). Rancangan: rantai/RANCANGAN-stok-opname-20261124.md.
-- ADITIF: tabel baru + trigger penjaga di inventory_ledger yang HANYA bertindak bila ada opname berstatus 'hitung' (0 baris lama disentuh,
-- tanpa backfill). Flag gelap stok_opname_v1 (tidak dinyalakan di tenant mana pun oleh migrasi ini).
-- Pembalik: V509__stok_opname_ROLLBACK.sql.

CREATE TABLE stock_opname_sequences (
    tenant_id   TEXT NOT NULL,
    year_month  VARCHAR(7) NOT NULL,
    last_number INT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, year_month)
);

CREATE OR REPLACE FUNCTION generate_stock_opname_number(p_tenant_id TEXT, p_tanggal DATE) RETURNS VARCHAR AS $$
DECLARE v_ym VARCHAR(7); v_n INT;
BEGIN
    v_ym := TO_CHAR(p_tanggal, 'YYYY-MM');
    INSERT INTO stock_opname_sequences (tenant_id, year_month, last_number) VALUES (p_tenant_id, v_ym, 1)
    ON CONFLICT (tenant_id, year_month) DO UPDATE SET last_number = stock_opname_sequences.last_number + 1
    RETURNING last_number INTO v_n;
    RETURN 'OPN-' || TO_CHAR(p_tanggal, 'YYMM') || '-' || LPAD(v_n::TEXT, 4, '0');
END;
$$ LANGUAGE plpgsql;

CREATE TABLE stock_opnames (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL,
    opname_number    VARCHAR(50) NOT NULL,
    warehouse_id     UUID NOT NULL REFERENCES warehouses(id),
    opname_date      DATE NOT NULL,
    reference_no     VARCHAR(100),
    notes            TEXT,
    status           VARCHAR(10) NOT NULL DEFAULT 'draft',
    adjustment_id    UUID NULL REFERENCES stock_adjustments(id),
    started_at       TIMESTAMPTZ, started_by UUID,
    posted_at        TIMESTAMPTZ, posted_by UUID,
    voided_at        TIMESTAMPTZ, voided_by UUID, voided_reason TEXT,
    cancelled_at     TIMESTAMPTZ, cancelled_by UUID, cancelled_reason TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       UUID NOT NULL,
    CONSTRAINT uq_opname_tenant_number UNIQUE (tenant_id, opname_number),
    CONSTRAINT chk_opname_status CHECK (status IN ('draft', 'hitung', 'posted', 'void', 'batal'))
);
-- SATU opname aktif (hitung) per gudang: pagar di DB, bukan konvensi kode (ironlaws: predikat atas baris -> DB).
CREATE UNIQUE INDEX uq_opname_gudang_terkunci ON stock_opnames (tenant_id, warehouse_id) WHERE status = 'hitung';
CREATE INDEX idx_opname_tenant_status ON stock_opnames (tenant_id, status, opname_date DESC);
ALTER TABLE stock_opnames ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_stock_opnames ON stock_opnames FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE TABLE stock_opname_lines (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opname_id         UUID NOT NULL REFERENCES stock_opnames(id) ON DELETE CASCADE,
    tenant_id         TEXT NOT NULL,
    product_id        UUID NOT NULL,
    product_code      VARCHAR(50),
    product_name      VARCHAR(255) NOT NULL,
    unit              VARCHAR(50),
    line_number       INT NOT NULL DEFAULT 1,
    system_quantity   NUMERIC(15,4),            -- SNAPSHOT saat mulai hitung (Σ ledger gudang), hanya tampilan/peringatan
    physical_quantity NUMERIC(15,4),            -- NULL = belum dihitung = TIDAK disesuaikan (putusan pemilik 24 Nov)
    counted_by        UUID, counted_at TIMESTAMPTZ, note TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_opname_baris UNIQUE (opname_id, product_id),
    CONSTRAINT chk_opname_fisik_nonneg CHECK (physical_quantity IS NULL OR physical_quantity >= 0)
);
CREATE INDEX idx_opname_lines_opname ON stock_opname_lines (opname_id);
ALTER TABLE stock_opname_lines ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_stock_opname_lines ON stock_opname_lines FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- PENJAGA GUDANG TERKUNCI: semua gerakan stok baru (juga pembalik/void) ke gudang yang sedang dihitung DITOLAK, kecuali gerakan
-- Penyesuaian Stok milik opname itu sendiri (source_type STOCK_ADJUSTMENT dgn source_id = stock_opnames.adjustment_id).
-- Jaring untuk jalur inline lama (sales_invoices, sales_receipts, transactions, stock_transfers) yang tak lewat helper.
CREATE OR REPLACE FUNCTION trg_tolak_gerak_gudang_terkunci() RETURNS TRIGGER AS $$
DECLARE v_nomor TEXT;
BEGIN
    IF NEW.warehouse_id IS NULL THEN RETURN NEW; END IF;
    SELECT o.opname_number INTO v_nomor FROM stock_opnames o
     WHERE o.tenant_id = NEW.tenant_id AND o.warehouse_id = NEW.warehouse_id AND o.status = 'hitung'
       AND NOT (NEW.source_type = 'STOCK_ADJUSTMENT' AND o.adjustment_id IS NOT NULL AND NEW.source_id::text = o.adjustment_id::text)
     LIMIT 1;
    IF v_nomor IS NOT NULL THEN
        RAISE EXCEPTION 'GUDANG_TERKUNCI: gudang sedang dihitung (%); selesaikan atau batalkan opname dulu.', v_nomor
            USING ERRCODE = 'P0409';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_gudang_terkunci BEFORE INSERT ON inventory_ledger FOR EACH ROW EXECUTE FUNCTION trg_tolak_gerak_gudang_terkunci();
