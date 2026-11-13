-- ROLLBACK V496: buang kolom syarat bayar PO. Kode BUG-205 membaca kolom ini (INSERT), jadi kembalikan KODE ke sebelum BUG-205 SEBELUM menjalankan berkas ini.
ALTER TABLE purchase_orders DROP CONSTRAINT IF EXISTS chk_po_payment_terms_days;
ALTER TABLE purchase_orders DROP COLUMN IF EXISTS payment_terms_days;
