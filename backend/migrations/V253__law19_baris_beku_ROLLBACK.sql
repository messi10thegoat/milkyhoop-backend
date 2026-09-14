BEGIN;
DROP TRIGGER trg_law19_baris_beku ON sales_invoice_items;
DROP TRIGGER trg_law19_baris_beku ON bill_items;
DROP TRIGGER trg_law19_baris_beku ON expense_items;
DROP FUNCTION law19_bekukan_baris();
DELETE FROM schema_migrations WHERE version = 'V253__law19_baris_beku.sql';
COMMIT;
