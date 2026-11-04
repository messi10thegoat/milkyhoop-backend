-- V477 TERIMA-TABEL (4 Nov 2026, BACKEND2; kontrak WORKSPACE kontrak-be-terima-tabel.md, desain disetujui MASTER). ADITIF.
-- (1) barang DITOLAK saat terima: dicatat di baris GRN saja -- BUKAN stok/HPP/jurnal/tagihan, tak mengurangi sisa PO (pola NetSuite/Odoo).
-- (2) toleransi lebih-terima per tenant (purchase_settings; tanpa baris / 0 = perilaku lama). Pola order_code_settings.
-- Pembalik: V477__terima_ditolak_toleransi_ROLLBACK.sql.
ALTER TABLE goods_receipt_items ADD COLUMN IF NOT EXISTS quantity_rejected NUMERIC(18,4) NOT NULL DEFAULT 0;
ALTER TABLE goods_receipt_items ADD COLUMN IF NOT EXISTS reject_reason TEXT;
ALTER TABLE goods_receipt_items DROP CONSTRAINT IF EXISTS goods_receipt_items_ditolak_cek;
ALTER TABLE goods_receipt_items ADD CONSTRAINT goods_receipt_items_ditolak_cek
    CHECK (quantity_rejected >= 0 AND (reject_reason IS NULL OR length(reject_reason) <= 200));

CREATE TABLE IF NOT EXISTS purchase_settings (
    tenant_id TEXT PRIMARY KEY REFERENCES "Tenant"(id),
    penerimaan_toleransi_persen NUMERIC(5,2) NOT NULL DEFAULT 0
        CONSTRAINT purchase_settings_toleransi_cek CHECK (penerimaan_toleransi_persen >= 0 AND penerimaan_toleransi_persen <= 100),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);
