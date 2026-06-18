-- V184: register ALL missing journal_source_types (FK fk_je_source_type ON DELETE RESTRICT).
-- 17 source_types are emitted to journal_entries by code but were unregistered -> silent 500 on never-run paths.
-- Class: invoice-void recognition/COGS reversal, cheques module (8), refunds (3), POS/receipt COGS companions (2), document-intake (1).
INSERT INTO journal_source_types (source_type, description, is_active) VALUES
  ('INVOICE_REVENUE_REVERSAL', 'Reversal pengakuan pendapatan faktur saat void', true),
  ('INVOICE_FULFILLMENT_REVERSAL', 'Reversal fulfillment (pendapatan+HPP) faktur saat void', true),
  ('SALES_INVOICE_COGS_REVERSAL', 'Reversal HPP faktur penjualan legacy saat void', true),
  ('SALES_RECEIPT_COGS', 'HPP untuk sales receipt (penjualan tunai)', true),
  ('POS_COGS', 'HPP untuk penjualan POS', true),
  ('DOCUMENT_INTAKE', 'Jurnal auto-posting dari document intake / kernel executor', true),
  ('DEPOSIT_REFUND', 'Refund uang muka pelanggan/vendor', true),
  ('CREDIT_NOTE_REFUND', 'Refund tunai nota kredit pelanggan', true),
  ('VENDOR_CREDIT_REFUND', 'Refund tunai kredit vendor', true),
  ('CHEQUE_RECEIVED', 'Giro/cek diterima dari pelanggan', true),
  ('CHEQUE_ISSUED', 'Giro/cek diterbitkan ke vendor', true),
  ('CHEQUE_DEPOSIT', 'Giro/cek disetor ke bank', true),
  ('CHEQUE_BOUNCE_AR', 'Re-recognition piutang giro bouncing', true),
  ('CHEQUE_BOUNCE_CHARGES', 'Biaya bank giro bouncing', true),
  ('CHEQUE_BOUNCE_REVERSAL', 'Reversal settlement giro saat bounce', true),
  ('CHEQUE_CANCEL', 'Reversal saat giro dibatalkan', true),
  ('CHEQUE_DELETE', 'Reversal saat giro dihapus', true)
ON CONFLICT (source_type) DO NOTHING;
