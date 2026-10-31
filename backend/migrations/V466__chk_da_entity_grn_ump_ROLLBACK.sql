-- V466 ROLLBACK: kembali ke 25 nilai V268. GAGAL KERAS bila sudah ada lampiran goods_receipt / vendor_deposit (tak menghapus data diam-diam).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM document_attachments WHERE entity_type IN ('goods_receipt','vendor_deposit')) THEN
    RAISE EXCEPTION 'V466 rollback: masih ada lampiran goods_receipt/vendor_deposit';
  END IF;
END $$;
ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (entity_type IN (
  'sales_invoice','bill','expense','customer','vendor','item','journal','quote',
  'purchase_order','sales_order','sales_receipt','payment','credit_note','vendor_credit',
  'stock_adjustment','stock_transfer','employee','asset','project','contract',
  'chat_message','other','customer_deposit','proforma','delivery'
));
DELETE FROM schema_migrations WHERE version = 'V466__chk_da_entity_grn_ump.sql';
