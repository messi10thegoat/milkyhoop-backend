-- ROLLBACK V263: cabut trigger doc_changed di sales_orders. Fungsi notify_doc_changed() dibiarkan.
DROP TRIGGER IF EXISTS trg_notify_doc_changed ON sales_orders;
