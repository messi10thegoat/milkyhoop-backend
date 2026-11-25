-- V510: LAPISAN BIAYA FIFO (putusan pemilik LANGSUNG 24 Nov 2026, T1-T8). Rancangan: rantai/RANCANGAN-fifo-20261124.md.
-- ADITIF: 2 tabel + 2 kolom, tanpa backfill; tak ada jalur kode yang memakainya sampai flag fifo_v1 (tidak dinyalakan di sini).
-- Barang diperlakukan FIFO hanya bila costing_method='fifo' DAN costing_mulai terisi DAN flag nyala.
-- JANGAN dijalankan di prod sebelum MASTER + pemilik menyetujui pelaksanaannya. Pembalik: V510__fifo_lapisan_ROLLBACK.sql.

ALTER TABLE products ADD COLUMN costing_mulai DATE;
ALTER TABLE tenant_config ADD COLUMN costing_method_bawaan TEXT NOT NULL DEFAULT 'weighted_average'
    CONSTRAINT chk_tc_costing_bawaan CHECK (costing_method_bawaan IN ('weighted_average', 'fifo'));

CREATE TABLE inventory_cost_layers (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seq              BIGINT GENERATED ALWAYS AS IDENTITY,          -- urutan TERCATAT (T7)
    tenant_id        TEXT NOT NULL,
    product_id       UUID NOT NULL REFERENCES products(id),        -- per BARANG, bukan gudang (T2)
    source_ledger_id UUID REFERENCES inventory_ledger(id),         -- NULL hanya untuk lapisan pembuka
    jenis            VARCHAR(10) NOT NULL DEFAULT 'masuk',
    layer_date       DATE NOT NULL,
    batch_id         UUID REFERENCES item_batches(id),             -- keterangan; biaya tetap FIFO (T3)
    qty_awal         NUMERIC(15,4) NOT NULL CHECK (qty_awal > 0),
    nilai_awal       NUMERIC(18,2) NOT NULL CHECK (nilai_awal >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_lapisan_jenis CHECK (jenis IN ('masuk', 'pembuka')),
    CONSTRAINT chk_lapisan_sumber CHECK ((jenis = 'pembuka') = (source_ledger_id IS NULL))
);
CREATE UNIQUE INDEX uq_lapisan_ledger ON inventory_cost_layers (source_ledger_id) WHERE source_ledger_id IS NOT NULL;
CREATE UNIQUE INDEX uq_lapisan_pembuka ON inventory_cost_layers (tenant_id, product_id) WHERE jenis = 'pembuka';
CREATE INDEX idx_lapisan_urut ON inventory_cost_layers (tenant_id, product_id, layer_date, seq);

CREATE TABLE inventory_layer_moves (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seq        BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id  TEXT NOT NULL,
    layer_id   UUID NOT NULL REFERENCES inventory_cost_layers(id),
    ledger_id  UUID NOT NULL REFERENCES inventory_ledger(id),
    jenis      VARCHAR(10) NOT NULL,
    qty        NUMERIC(15,4) NOT NULL,                             -- bertanda: pakai < 0, balik > 0, revaluasi = 0
    nilai      NUMERIC(18,2) NOT NULL,                             -- bertanda
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_gerak_lapisan CHECK (
        (jenis = 'pakai' AND qty < 0 AND nilai <= 0) OR (jenis = 'balik' AND qty > 0 AND nilai >= 0) OR (jenis = 'revaluasi' AND qty = 0 AND nilai <> 0))
);
CREATE INDEX idx_gerak_lapisan ON inventory_layer_moves (layer_id);
CREATE INDEX idx_gerak_ledger ON inventory_layer_moves (tenant_id, ledger_id);

-- Sisa lapisan = awal + Σ gerak; tak pernah negatif, tak pernah melebihi awal, dan qty habis = nilai habis.
CREATE FUNCTION trg_jaga_gerak_lapisan() RETURNS trigger AS $$
DECLARE l inventory_cost_layers%ROWTYPE; q NUMERIC; n NUMERIC;
BEGIN
    SELECT * INTO l FROM inventory_cost_layers WHERE id = NEW.layer_id FOR UPDATE;
    IF l.tenant_id IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'FIFO_TENANT: gerak lapisan beda tenant';
    END IF;
    SELECT COALESCE(SUM(m.qty), 0), COALESCE(SUM(m.nilai), 0) INTO q, n FROM inventory_layer_moves m WHERE m.layer_id = NEW.layer_id;
    q := l.qty_awal + q + NEW.qty;
    n := l.nilai_awal + n + NEW.nilai;
    IF q < 0 OR n < 0 OR q > l.qty_awal OR (q = 0 AND n <> 0) THEN
        RAISE EXCEPTION 'FIFO_SISA: lapisan % menjadi qty % nilai %', NEW.layer_id, q, n;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_jaga_gerak_lapisan BEFORE INSERT ON inventory_layer_moves FOR EACH ROW EXECUTE FUNCTION trg_jaga_gerak_lapisan();

CREATE FUNCTION trg_lapisan_tambah_saja() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'FIFO_TAMBAH_SAJA: % hanya boleh ditambah (Law 3)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_lapisan_tambah_saja BEFORE UPDATE OR DELETE ON inventory_cost_layers FOR EACH ROW EXECUTE FUNCTION trg_lapisan_tambah_saja();
CREATE TRIGGER trg_gerak_tambah_saja BEFORE UPDATE OR DELETE ON inventory_layer_moves FOR EACH ROW EXECUTE FUNCTION trg_lapisan_tambah_saja();

-- T1: FIFO -> rata-rata ditolak sesudah ada lapisan bertransaksi; costing_mulai beku bersamanya.
CREATE FUNCTION trg_jaga_metode_biaya() RETURNS trigger AS $$
BEGIN
    IF (NEW.costing_method IS DISTINCT FROM OLD.costing_method OR NEW.costing_mulai IS DISTINCT FROM OLD.costing_mulai)
       AND OLD.costing_mulai IS NOT NULL
       AND EXISTS (SELECT 1 FROM inventory_cost_layers l WHERE l.tenant_id = OLD.tenant_id AND l.product_id = OLD.id
                   AND (l.jenis = 'masuk' OR EXISTS (SELECT 1 FROM inventory_layer_moves m WHERE m.layer_id = l.id))) THEN
        RAISE EXCEPTION 'FIFO_METODE_TERKUNCI: barang sudah punya transaksi FIFO';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_jaga_metode_biaya BEFORE UPDATE OF costing_method, costing_mulai ON products FOR EACH ROW EXECUTE FUNCTION trg_jaga_metode_biaya();

ALTER TABLE inventory_cost_layers ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_inventory_cost_layers ON inventory_cost_layers FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));
ALTER TABLE inventory_layer_moves ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_inventory_layer_moves ON inventory_layer_moves FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));
