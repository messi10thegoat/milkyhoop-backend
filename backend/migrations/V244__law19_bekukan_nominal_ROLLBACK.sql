-- ROLLBACK V244 — cabut pagar nominal Law 19 (kembali ke keadaan TANPA pagar).
-- NOL data disentuh oleh V244 maupun rollback ini.
BEGIN;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON sales_invoices;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON bills;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON expenses;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON receive_payments;
DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON bill_payments_v2;
DROP FUNCTION IF EXISTS law19_bekukan_nominal();
COMMIT;
