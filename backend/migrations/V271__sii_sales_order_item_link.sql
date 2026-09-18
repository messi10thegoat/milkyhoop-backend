-- V271__sii_sales_order_item_link.sql
-- Item 7 (MASTER Sabtu list): voiding an invoice created from a sales order must
-- decrement sales_order_items.quantity_invoiced (it was incremented at create-invoice,
-- sales_orders.py:1426, but void_invoice never reversed it -> SO line stuck 'invoiced').
--
-- The per-line link between an invoice line and the SO line it invoiced was NOT stored
-- (sales_invoice_items only had item_id = product), so a void could not know which SO
-- line to decrement — and product-matching is ambiguous when a SO has the same product
-- on >1 line (1 such group exists on kaos). This adds the explicit link.

ALTER TABLE sales_invoice_items
    ADD COLUMN IF NOT EXISTS sales_order_item_id UUID
        REFERENCES sales_order_items(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_sii_sales_order_item
    ON sales_invoice_items(sales_order_item_id);

-- Backfill ONLY unambiguous links (invoice from a SO; product maps to exactly ONE SO
-- line). Ambiguous duplicate-product cases stay NULL (those invoices are not voided;
-- forward create-invoice will store the exact link).
UPDATE sales_invoice_items sii
SET sales_order_item_id = m.soi_id
FROM (
    SELECT sii2.id AS sii_id, soi.id AS soi_id
    FROM sales_invoice_items sii2
    JOIN sales_invoices si ON si.id = sii2.invoice_id AND si.sales_order_id IS NOT NULL
    JOIN sales_order_items soi
        ON soi.sales_order_id = si.sales_order_id AND soi.item_id = sii2.item_id
    WHERE sii2.sales_order_item_id IS NULL
      AND (SELECT COUNT(*) FROM sales_order_items soi2
           WHERE soi2.sales_order_id = si.sales_order_id
             AND soi2.item_id = sii2.item_id) = 1
) m
WHERE sii.id = m.sii_id;
