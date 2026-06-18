-- V185: widen chk_sr_status to allow 'draft' for sales_receipts (cash sale create flow).
--
-- Forward-path defect: create_sales_receipt() inserts the header as 'completed',
-- then inserts line items whose AFTER-INSERT trigger (trg_update_sr_totals ->
-- update_sales_receipt_totals) UPDATEs the parent's subtotal/tax/total. That UPDATE
-- on an already-'completed' row trips trg_prevent_sr_modification (prevent_sr_modification),
-- which RAISEs unless NEW.status='void' -> 500 before any journal (SALES_RECEIPT_COGS
-- registered by V184 was therefore unreachable).
--
-- Fix (mirrors sales invoice create->post): insert header as 'draft' so the totals
-- trigger can populate it, then flip 'draft'->'completed' at the end of the txn (an
-- UPDATE where OLD.status='draft' passes the modification guard). This requires the
-- status CHECK to permit 'draft'. prevent_sr_modification only guards completed/void
-- rows, so 'draft' rows remain freely mutable during creation.
--
-- Idempotent: drop + recreate the constraint.

ALTER TABLE sales_receipts DROP CONSTRAINT IF EXISTS chk_sr_status;

ALTER TABLE sales_receipts ADD CONSTRAINT chk_sr_status
  CHECK (status::text = ANY (ARRAY['draft','completed','void']::text[]));
