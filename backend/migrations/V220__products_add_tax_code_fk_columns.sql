-- V220: products — add sales_tax_id / purchase_tax_id (uuid FK -> tax_codes) that the item
-- autocomplete + Sales-Invoice item picker are built against but were never migrated.
--
-- GET /items/autocomplete (items.py:1663) SELECTs p.sales_tax_id / p.purchase_tax_id and
-- JOINs tax_codes ON st.id = p.sales_tax_id. Tested on live: ERROR "column p.sales_tax_id
-- does not exist" -> 500 at PLAN time, before any tax value is read (identical for non-PKP
-- tenants; the broken picker blocks steps 1 and 5, not the tax math).
-- FE consumes them: SalesInvoice CreateInvoice/ItemFormSheet.tsx types sales_tax_id/
-- sales_tax_name/sales_tax_rate as "From /api/items/autocomplete" and prefills the
-- invoice-line tax from selectedProduct.sales_tax_id/name/rate. Built-but-unmigrated, same
-- class as the V219 quotes columns -> ADD (FE-oracle rule: consumer reads it -> add, don't strip).
--
-- SEPARATE MIGRATION (not folded into V219) on purpose: V219 is already committed with a
-- quotes-specific name/semantic; editing a committed migration violates immutability even
-- though no live/persistent DB holds its checksum yet. Separate = clean provenance + revert.
--
-- products.sales_tax / purchase_tax (varchar) are a legacy model column, LEFT UNTOUCHED
-- (backlog, out of scope). tax_codes(id,name,rate) already exist. Fresh tenant = 0 product
-- rows -> no data backfill. Idempotent; FK targets tax_codes.id to match the items.py JOIN.

ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_tax_id    uuid REFERENCES tax_codes(id);
ALTER TABLE products ADD COLUMN IF NOT EXISTS purchase_tax_id uuid REFERENCES tax_codes(id);
