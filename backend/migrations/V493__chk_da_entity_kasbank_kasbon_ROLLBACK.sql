-- V493 ROLLBACK: kembali ke 27 nilai V466. GAGAL KERAS bila sudah ada lampiran bank_transaction / bank_transfer / employee_advance
-- (tak menghapus data diam-diam).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM document_attachments WHERE entity_type IN ('bank_transaction','bank_transfer','employee_advance')) THEN
    RAISE EXCEPTION 'V493 rollback: masih ada lampiran bank_transaction/bank_transfer/employee_advance';
  END IF;
END $$;
ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (entity_type IN (
  'sales_invoice','bill','expense','customer','vendor','item','journal','quote',
  'purchase_order','sales_order','sales_receipt','payment','credit_note','vendor_credit',
  'stock_adjustment','stock_transfer','employee','asset','project','contract',
  'chat_message','other','customer_deposit','proforma','delivery',
  'goods_receipt','vendor_deposit'
));
DELETE FROM schema_migrations WHERE version = 'V493__chk_da_entity_kasbank_kasbon.sql';
