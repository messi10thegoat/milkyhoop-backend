-- V125: Add indexes on products.sales_tax_id and products.purchase_tax_id
-- Columns + FKs already exist; this adds indexes for query performance.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_sales_tax_id ON products(sales_tax_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_purchase_tax_id ON products(purchase_tax_id);
