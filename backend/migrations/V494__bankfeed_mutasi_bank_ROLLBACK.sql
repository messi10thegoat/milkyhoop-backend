-- V494 ROLLBACK: hapus tabel Bank Feed + kembalikan bank_reconciliations & chk_da_entity (V493). GAGAL KERAS bila sudah dipakai
-- (ada baris mutasi/impor/aturan, lampiran mutasi_bank_impor, atau rekonsiliasi) -- tak menghapus data diam-diam.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM mutasi_bank_impor) OR EXISTS (SELECT 1 FROM mutasi_bank) OR EXISTS (SELECT 1 FROM aturan_bank)
     OR EXISTS (SELECT 1 FROM mutasi_bank_tautan) OR EXISTS (SELECT 1 FROM bank_reconciliations)
     OR EXISTS (SELECT 1 FROM document_attachments WHERE entity_type = 'mutasi_bank_impor') THEN
    RAISE EXCEPTION 'V494 rollback: tabel Bank Feed / rekonsiliasi sudah berisi data';
  END IF;
END $$;
DROP TABLE mutasi_bank_tautan;
DROP TABLE mutasi_bank;
DROP TABLE mutasi_bank_impor;
DROP TABLE aturan_bank;
ALTER TABLE bank_reconciliations
  DROP COLUMN dibatalkan_oleh, DROP COLUMN dibatalkan_pada, DROP COLUMN alasan_batal,
  ALTER COLUMN difference TYPE bigint, ALTER COLUMN reconciled_deposits TYPE bigint,
  ALTER COLUMN reconciled_withdrawals TYPE bigint, ALTER COLUMN unreconciled_deposits TYPE bigint,
  ALTER COLUMN unreconciled_withdrawals TYPE bigint;
ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;
ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (entity_type IN (
  'sales_invoice','bill','expense','customer','vendor','item','journal','quote',
  'purchase_order','sales_order','sales_receipt','payment','credit_note','vendor_credit',
  'stock_adjustment','stock_transfer','employee','asset','project','contract',
  'chat_message','other','customer_deposit','proforma','delivery',
  'goods_receipt','vendor_deposit',
  'bank_transaction','bank_transfer','employee_advance'
));
DELETE FROM schema_migrations WHERE version = 'V494__bankfeed_mutasi_bank.sql';
