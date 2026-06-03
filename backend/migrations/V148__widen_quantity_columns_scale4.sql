-- V148: Widen transaction line-item quantity columns to scale 4 (decimal qty support)
-- Frontend will start sending decimal quantities (e.g. 3.5 kg). Existing line-item
-- quantity columns were scale 2 (or integer for bonus_qty), truncating 3 decimals.
-- This widens scale to 4 keeping/raising precision. SAFE: additive precision widening,
-- no data loss (all current values fit). Stock/BOM/WO columns already numeric(15,4) —
-- left untouched. base_quantity columns are numeric(18,6) — left untouched.
--
-- Before -> After:
--   bill_items.quantity                numeric(10,2)  -> numeric(15,4)
--   sales_invoice_items.quantity       numeric(10,2)  -> numeric(15,4)
--   sales_invoice_items.fulfilled_qty  numeric(18,2)  -> numeric(18,4)
--   sales_invoices.total_fulfilled_qty numeric(18,2)  -> numeric(18,4)
--   invoice_fulfillment_items.quantity numeric(18,2)  -> numeric(18,4)
--   bill_items.bonus_qty               integer        -> numeric(15,4)

BEGIN;

ALTER TABLE bill_items
    ALTER COLUMN quantity TYPE numeric(15,4);

ALTER TABLE bill_items
    ALTER COLUMN bonus_qty TYPE numeric(15,4);

ALTER TABLE sales_invoice_items
    ALTER COLUMN quantity TYPE numeric(15,4);

ALTER TABLE sales_invoice_items
    ALTER COLUMN fulfilled_qty TYPE numeric(18,4);

ALTER TABLE sales_invoices
    ALTER COLUMN total_fulfilled_qty TYPE numeric(18,4);

ALTER TABLE invoice_fulfillment_items
    ALTER COLUMN quantity TYPE numeric(18,4);

COMMIT;
