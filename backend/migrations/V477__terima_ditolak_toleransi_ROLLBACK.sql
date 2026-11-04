-- V477 ROLLBACK: catatan barang ditolak + toleransi IKUT HILANG -- jalankan hanya bila kode pemakainya sudah dikembalikan.
DROP TABLE IF EXISTS purchase_settings;
-- GRN hanya-ditolak (quantity 0) wajib sudah tak ada / di-void dulu, kalau tidak CHECK V452 menolak
ALTER TABLE goods_receipt_items DROP CONSTRAINT IF EXISTS goods_receipt_items_quantity_check;
ALTER TABLE goods_receipt_items ADD CONSTRAINT goods_receipt_items_quantity_check CHECK (quantity > 0);
ALTER TABLE goods_receipt_items DROP CONSTRAINT IF EXISTS goods_receipt_items_ditolak_cek;
ALTER TABLE goods_receipt_items DROP COLUMN IF EXISTS reject_reason;
ALTER TABLE goods_receipt_items DROP COLUMN IF EXISTS quantity_rejected;
