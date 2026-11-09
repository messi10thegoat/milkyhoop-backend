-- V493: LAMPIRAN Kas & Bank + Kasbon (9 Nov 2026) -- izinkan entity_type bank_transaction (mutasi masuk/keluar + hitung fisik),
-- bank_transfer, employee_advance di document_attachments (pabrik routers/lampiran_induk). Hanya MENAMBAH tiga nilai pada CHECK;
-- nol baris berubah. Disetujui LANGSUNG pemilik di sesi BACKEND 9 Nov 2026 (AskUserQuestion). Pembalik: V493__..._ROLLBACK.sql
-- (gagal keras bila sudah dipakai).
ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (entity_type IN (
  'sales_invoice','bill','expense','customer','vendor','item','journal','quote',
  'purchase_order','sales_order','sales_receipt','payment','credit_note','vendor_credit',
  'stock_adjustment','stock_transfer','employee','asset','project','contract',
  'chat_message','other','customer_deposit','proforma','delivery',
  'goods_receipt','vendor_deposit',
  'bank_transaction','bank_transfer','employee_advance'
));
