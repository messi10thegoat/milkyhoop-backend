-- V121: Link uploaded_documents to transaction objects (bill, invoice, expense, etc.)
-- Supports Tahap 1: Kernel Document Executor refactor

ALTER TABLE uploaded_documents 
  ADD COLUMN IF NOT EXISTS transaction_type TEXT,
  ADD COLUMN IF NOT EXISTS transaction_id UUID;

COMMENT ON COLUMN uploaded_documents.transaction_type IS 'bill, sales_invoice, expense, bill_payment, receive_payment, bank_transfer, journal_entry';
COMMENT ON COLUMN uploaded_documents.transaction_id IS 'FK to transaction table corresponding to transaction_type';

CREATE INDEX IF NOT EXISTS idx_uploaded_docs_transaction 
  ON uploaded_documents(transaction_type, transaction_id) 
  WHERE transaction_id IS NOT NULL;
