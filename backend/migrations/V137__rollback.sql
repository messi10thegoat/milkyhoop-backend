-- V137__rollback.sql
-- Rollback for V137__three_event_revenue_recognition.sql
-- All statements idempotent. Do NOT execute unless instructed.

BEGIN;

-- Drop new tables (cascade drops indexes)
DROP TABLE IF EXISTS invoice_fulfillment_items CASCADE;
DROP TABLE IF EXISTS invoice_fulfillments CASCADE;

-- Remove CHECK constraints
ALTER TABLE sales_invoices DROP CONSTRAINT IF EXISTS chk_si_fulfillment_status;
ALTER TABLE sales_invoices DROP CONSTRAINT IF EXISTS chk_si_revenue_status;

-- Remove columns from sales_invoice_items
ALTER TABLE sales_invoice_items
    DROP COLUMN IF EXISTS allocated_amount,
    DROP COLUMN IF EXISTS fulfilled_qty,
    DROP COLUMN IF EXISTS recognized_amount;

-- Remove columns from sales_invoices
ALTER TABLE sales_invoices
    DROP COLUMN IF EXISTS fulfillment_status,
    DROP COLUMN IF EXISTS revenue_status,
    DROP COLUMN IF EXISTS fulfillment_journal_id,
    DROP COLUMN IF EXISTS revenue_journal_id,
    DROP COLUMN IF EXISTS total_fulfilled_qty,
    DROP COLUMN IF EXISTS total_recognized_amount;

-- Delete CoA 2-10700 rows (only system-inserted ones)
DELETE FROM chart_of_accounts WHERE account_code = '2-10700' AND is_system = true;

COMMIT;
