-- V515: ALOKASI BATCH per baris Faktur Penjualan (paritas butir 9, ikut Accurate; GO pemilik LANGSUNG 27 Nov ±20:30 di sesi BACKEND 3).
-- ADITIF, 0 data. Satu baris faktur boleh diambil dari beberapa batch (qty SATUAN DASAR); HPP tetap per barang (T3).
-- Pembalik: V515__alokasi_batch_baris_faktur_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig).
CREATE TABLE sales_invoice_item_batches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    invoice_item_id UUID NOT NULL REFERENCES sales_invoice_items(id) ON DELETE CASCADE,
    batch_id        UUID NOT NULL REFERENCES item_batches(id),
    quantity        NUMERIC(15,4) NOT NULL CONSTRAINT chk_siib_qty CHECK (quantity > 0),
    urutan          INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_siib_baris_batch UNIQUE (invoice_item_id, batch_id)
);
CREATE INDEX idx_siib_tenant_batch ON sales_invoice_item_batches (tenant_id, batch_id);
ALTER TABLE sales_invoice_item_batches ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_sales_invoice_item_batches ON sales_invoice_item_batches FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));
