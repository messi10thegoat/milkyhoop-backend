-- V163: Complete journal_source_types lookup (Fase G-3 surprise #7)
-- V160 (F5) seeded from HISTORICAL DB usage. Modules that never posted
-- (payroll first, fase 4-11 sales/fulfillment later) had their source_types
-- omitted, blocking posting via FK constraint.
-- Lesson: enumerate from CODE, not from historical data.
-- Comprehensive grep of all source_type emissions in routers/services.

INSERT INTO journal_source_types (source_type, description) VALUES
  ('ADJUSTMENT','Generic adjustment'),
  ('ASSET_DISPOSAL','Fixed asset disposal'),
  ('ASSET_SALE','Fixed asset sale'),
  ('BILL_PAYMENT_VOID','Bill payment void/reversal'),
  ('CASH_SALE','Cash sale (POS / non-invoice)'),
  ('DEPOSIT_APPLICATION','Customer/vendor deposit application'),
  ('DEPRECIATION','Fixed asset depreciation'),
  ('FG_RECEIPT','Finished goods receipt'),
  ('FIXED_ASSET','Fixed asset acquisition'),
  ('INTERCOMPANY','Intercompany (parked, future)'),
  ('OPENING_BALANCE','Per-account opening balance'),
  ('PAYMENT_MADE','Payment made out'),
  ('PAYMENT_REQUEST','Payment request'),
  ('PAYROLL','Payroll run posting'),
  ('PAYROLL_BPJS_PAYMENT','BPJS remittance payment'),
  ('PAYROLL_PAYMENT','Payroll payment to employee'),
  ('PAYROLL_TAX_PAYMENT','PPh21 remittance payment'),
  ('POS_SALE','POS sale transaction'),
  ('PURCHASE_INVOICE','Purchase invoice'),
  ('RECONCILIATION_ADJUSTMENT','Bank reconciliation adjustment'),
  ('SALES_INVOICE','Sales invoice'),
  ('SALES_INVOICE_VOID','Sales invoice void/reversal'),
  ('SALES_RECEIPT','Sales receipt'),
  ('STOCK_TRANSFER','Inter-warehouse stock transfer'),
  ('VENDOR_OPENING_BALANCE','Vendor opening balance')
ON CONFLICT (source_type) DO NOTHING;
