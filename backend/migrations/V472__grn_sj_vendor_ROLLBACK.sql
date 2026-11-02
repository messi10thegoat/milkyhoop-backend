-- V472 ROLLBACK: cabut No. SJ vendor GRN. Nilai yang sudah diisi pengguna IKUT HILANG (kolom dibuang) -- jalankan hanya bila
-- kode yang memakainya sudah dikembalikan (kode lama tak membaca kolom ini).
DROP INDEX IF EXISTS idx_goods_receipts_sj_vendor;
ALTER TABLE goods_receipts DROP CONSTRAINT IF EXISTS goods_receipts_sj_vendor_cek;
ALTER TABLE goods_receipts DROP COLUMN IF EXISTS vendor_delivery_note;
