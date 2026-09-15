-- ROLLBACK V259: cabut trigger & fungsi notify realtime.
DROP TRIGGER IF EXISTS trg_notify_doc_changed ON sales_invoices;
DROP TRIGGER IF EXISTS trg_notify_membership_changed ON user_tenant_roles;
DROP FUNCTION IF EXISTS notify_doc_changed();
DROP FUNCTION IF EXISTS notify_membership_changed();
