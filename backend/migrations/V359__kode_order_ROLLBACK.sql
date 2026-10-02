-- Rollback V359: buang kode order (data kode yang sudah terbit IKUT HILANG -- pg_dump apply_mig adalah cadangannya).
CREATE OR REPLACE FUNCTION fn_search_text_sales_orders() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.order_number, NEW.customer_name, NEW.reference, NEW.shipping_address, NEW.notes)));
  RETURN NEW;
END $$;
DROP TABLE IF EXISTS order_code_events;
DROP TABLE IF EXISTS order_code_counters;
DROP TABLE IF EXISTS order_code_settings;
DROP INDEX IF EXISTS uq_so_order_code;
ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS chk_so_order_code_source;
ALTER TABLE sales_orders DROP COLUMN IF EXISTS order_code_source;
ALTER TABLE sales_orders DROP COLUMN IF EXISTS order_title;
ALTER TABLE sales_orders DROP COLUMN IF EXISTS order_code;
