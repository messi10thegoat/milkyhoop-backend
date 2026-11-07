-- V485_ROLLBACK (BUG-069): hanya bila kode pembaca return_bill_id BELUM live. Menghapus kolom rujukan; NOL jurnal / angka.
DROP INDEX IF EXISTS idx_vendor_credits_return_bill;
ALTER TABLE vendor_credits DROP COLUMN IF EXISTS return_bill_number;
ALTER TABLE vendor_credits DROP COLUMN IF EXISTS return_bill_id;
