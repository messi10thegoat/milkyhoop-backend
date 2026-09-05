-- Rollback V238. Membuang kolom = membuang isinya; jalankan hanya kalau
-- yakin tak ada faktur yang sudah menyimpan nomor PO/DO.
ALTER TABLE sales_invoices
    DROP COLUMN IF EXISTS purchase_order_no,
    DROP COLUMN IF EXISTS delivery_order_no;
