-- V503: BUG-261 (17 Nov 2026) -- Penyesuaian Persediaan: gudang PER BARIS + baris yang menyelesaikan selisih terima Pemindahan (Transit). Dua kolom ADITIF, NULLABLE, tanpa default
-- dan TANPA backfill: warehouse_id NULL = ikut gudang header dokumen (perilaku lama utuh); source_transfer_id NULL = baris biasa. Tidak ada enum/CHECK baru.
-- DIIZINKAN LANGSUNG pemilik (17 Nov 08:40 WIB, via MASTER/AskUserQuestion). Pembalik: V503__penyesuaian_gudang_baris_sumber_pemindahan_ROLLBACK.sql. Tanpa BEGIN/COMMIT.
ALTER TABLE stock_adjustment_items ADD COLUMN IF NOT EXISTS warehouse_id UUID REFERENCES warehouses(id);
ALTER TABLE stock_adjustment_items ADD COLUMN IF NOT EXISTS source_transfer_id UUID REFERENCES stock_transfers(id);
COMMENT ON COLUMN stock_adjustment_items.warehouse_id IS 'Gudang baris (NULL = gudang header stock_adjustments.warehouse_id). Baris penyelesaian selisih Pemindahan = gudang sistem Transit.';
COMMENT ON COLUMN stock_adjustment_items.source_transfer_id IS 'Pemindahan Barang yang selisih terimanya diselesaikan baris ini (tertunda = dikirim - diterima - SUM baris penyesuaian POSTED bertaut).';
CREATE INDEX IF NOT EXISTS idx_sai_source_transfer ON stock_adjustment_items (source_transfer_id) WHERE source_transfer_id IS NOT NULL;
