-- ROLLBACK V261: cabut trigger doc_changed di bills + bill_payments_v2.
-- Fungsi notify_doc_changed() dibiarkan (dipakai sales_invoices/V259).
DROP TRIGGER IF EXISTS trg_notify_doc_changed ON bills;
DROP TRIGGER IF EXISTS trg_notify_doc_changed ON bill_payments_v2;
