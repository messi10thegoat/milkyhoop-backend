-- ROLLBACK V436: lepas kolom tautan (hanya kehilangan tautan, bukan uang; kode P-U2 harus sudah dipulihkan lebih dulu).
DROP INDEX IF EXISTS public.idx_bill_items_po_item;
ALTER TABLE public.bill_items DROP COLUMN IF EXISTS purchase_order_item_id;
