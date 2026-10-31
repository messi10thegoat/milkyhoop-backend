-- V466: Lampiran F2b (30-31 Okt 2026) -- izinkan entity_type goods_receipt + vendor_deposit di document_attachments (pabrik
-- routers/lampiran_induk: lampiran Penerimaan Barang + Uang Muka Pembelian). Hanya MENAMBAH dua nilai pada CHECK; nol baris berubah.
-- Disetujui LANGSUNG pemilik dengan menyebut V466. Pembalik: V466__chk_da_entity_grn_ump_ROLLBACK.sql (gagal keras bila sudah dipakai).
ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (entity_type IN (
  'sales_invoice','bill','expense','customer','vendor','item','journal','quote',
  'purchase_order','sales_order','sales_receipt','payment','credit_note','vendor_credit',
  'stock_adjustment','stock_transfer','employee','asset','project','contract',
  'chat_message','other','customer_deposit','proforma','delivery',
  'goods_receipt','vendor_deposit'
));
