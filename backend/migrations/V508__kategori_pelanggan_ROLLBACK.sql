-- Pembalik V508. Data kategori (tabel customer_categories) HILANG; kolom customers.category_id dibuang. Harga pelanggan tak berubah (tak ada backfill).
ALTER TABLE customers DROP CONSTRAINT IF EXISTS fk_cust_cat;
DROP INDEX IF EXISTS idx_customers_category;
ALTER TABLE customers DROP COLUMN IF EXISTS category_id;
DROP TABLE IF EXISTS customer_categories;
ALTER TABLE price_lists DROP CONSTRAINT IF EXISTS uq_price_lists_tenant_id;
