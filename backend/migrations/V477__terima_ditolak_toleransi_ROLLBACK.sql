-- V477 ROLLBACK: catatan barang ditolak + toleransi IKUT HILANG -- jalankan hanya bila kode pemakainya sudah dikembalikan.
DROP TABLE IF EXISTS purchase_settings;
ALTER TABLE goods_receipt_items DROP CONSTRAINT IF EXISTS goods_receipt_items_ditolak_cek;
ALTER TABLE goods_receipt_items DROP COLUMN IF EXISTS reject_reason;
ALTER TABLE goods_receipt_items DROP COLUMN IF EXISTS quantity_rejected;
