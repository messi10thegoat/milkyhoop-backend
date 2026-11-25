-- MN-1 (26 Nov 2026, BACKEND 3): stok minimum/maksimum PER GUDANG (Accurate: "Minimum/Maksimum Kuantitas" per gudang di master barang).
-- Master data, BUKAN kolom cache warehouse_stock (warehouse_stock.min_stock/max_stock ada tetapi tak dipakai kode dan dihapus oleh skrip reset transaksi).
-- Aditif: satu tabel baru, tanpa backfill, tanpa mengubah tabel lama. products.reorder_level tetap = minimum SEMUA gudang (perilaku Pesan Ulang tak berubah).
CREATE TABLE stok_minimum_gudang (
    tenant_id    TEXT NOT NULL,
    product_id   UUID NOT NULL REFERENCES products(id),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    minimum      NUMERIC(15,4) NOT NULL DEFAULT 0 CHECK (minimum >= 0),
    maksimum     NUMERIC(15,4) CHECK (maksimum IS NULL OR maksimum >= minimum),
    updated_by   UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, product_id, warehouse_id)
);
CREATE INDEX idx_stok_minimum_gudang_gudang ON stok_minimum_gudang (tenant_id, warehouse_id);
ALTER TABLE stok_minimum_gudang ENABLE ROW LEVEL SECURITY;
CREATE POLICY rls_stok_minimum_gudang ON stok_minimum_gudang FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));
