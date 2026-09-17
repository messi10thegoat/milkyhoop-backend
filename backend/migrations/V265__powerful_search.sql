-- V265: Powerful Search — pg_trgm + unaccent, trigger-maintained search_text + GIN.
-- Generated. Metadata-only (adds search_text column/trigger/index); no financial data touched.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- === customers ===
ALTER TABLE customers ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_customers() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.code, NEW.name, NEW.nama, NEW.display_name, NEW.company_name, NEW.contact_person, NEW.phone, NEW.phone2, NEW.telepon, NEW.mobile_phone, NEW.email, NEW.address, NEW.alamat, NEW.city, NEW.province, NEW.postal_code, NEW.tax_id, NEW.nik, NEW.nomor_member, NEW.community, NEW.website, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_customers ON customers;
CREATE TRIGGER trg_search_text_customers BEFORE INSERT OR UPDATE ON customers
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_customers();
UPDATE customers SET search_text = lower(unaccent(concat_ws(' ', code, name, nama, display_name, company_name, contact_person, phone, phone2, telepon, mobile_phone, email, address, alamat, city, province, postal_code, tax_id, nik, nomor_member, community, website, notes)));
CREATE INDEX IF NOT EXISTS idx_customers_search_trgm ON customers USING gin (search_text gin_trgm_ops);

-- === vendors ===
ALTER TABLE vendors ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_vendors() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.code, NEW.name, NEW.display_name, NEW.company_name, NEW.contact_person, NEW.phone, NEW.mobile_phone, NEW.email, NEW.address, NEW.city, NEW.province, NEW.postal_code, NEW.tax_id, NEW.nik, NEW.bank_name, NEW.bank_account_number, NEW.bank_account_holder, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_vendors ON vendors;
CREATE TRIGGER trg_search_text_vendors BEFORE INSERT OR UPDATE ON vendors
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_vendors();
UPDATE vendors SET search_text = lower(unaccent(concat_ws(' ', code, name, display_name, company_name, contact_person, phone, mobile_phone, email, address, city, province, postal_code, tax_id, nik, bank_name, bank_account_number, bank_account_holder, notes)));
CREATE INDEX IF NOT EXISTS idx_vendors_search_trgm ON vendors USING gin (search_text gin_trgm_ops);

-- === sales_invoices ===
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_sales_invoices() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.invoice_number, NEW.customer_name, NEW.ref_no, NEW.notes, NEW.purchase_order_no, NEW.delivery_order_no, NEW.efaktur_number, NEW.customer_npwp, NEW.customer_nik)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_sales_invoices ON sales_invoices;
CREATE TRIGGER trg_search_text_sales_invoices BEFORE INSERT OR UPDATE ON sales_invoices
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_sales_invoices();
UPDATE sales_invoices SET search_text = lower(unaccent(concat_ws(' ', invoice_number, customer_name, ref_no, notes, purchase_order_no, delivery_order_no, efaktur_number, customer_npwp, customer_nik)));
CREATE INDEX IF NOT EXISTS idx_sales_invoices_search_trgm ON sales_invoices USING gin (search_text gin_trgm_ops);

-- === quotes ===
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_quotes() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.quote_number, NEW.customer_name, NEW.customer_email, NEW.reference, NEW.subject, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_quotes ON quotes;
CREATE TRIGGER trg_search_text_quotes BEFORE INSERT OR UPDATE ON quotes
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_quotes();
UPDATE quotes SET search_text = lower(unaccent(concat_ws(' ', quote_number, customer_name, customer_email, reference, subject, notes)));
CREATE INDEX IF NOT EXISTS idx_quotes_search_trgm ON quotes USING gin (search_text gin_trgm_ops);

-- === sales_orders ===
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_sales_orders() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.order_number, NEW.customer_name, NEW.reference, NEW.shipping_address, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_sales_orders ON sales_orders;
CREATE TRIGGER trg_search_text_sales_orders BEFORE INSERT OR UPDATE ON sales_orders
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_sales_orders();
UPDATE sales_orders SET search_text = lower(unaccent(concat_ws(' ', order_number, customer_name, reference, shipping_address, notes)));
CREATE INDEX IF NOT EXISTS idx_sales_orders_search_trgm ON sales_orders USING gin (search_text gin_trgm_ops);

-- === sales_order_shipments ===
ALTER TABLE sales_order_shipments ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_sales_order_shipments() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.shipment_number, NEW.carrier, NEW.tracking_number)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_sales_order_shipments ON sales_order_shipments;
CREATE TRIGGER trg_search_text_sales_order_shipments BEFORE INSERT OR UPDATE ON sales_order_shipments
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_sales_order_shipments();
UPDATE sales_order_shipments SET search_text = lower(unaccent(concat_ws(' ', shipment_number, carrier, tracking_number)));
CREATE INDEX IF NOT EXISTS idx_sales_order_shipments_search_trgm ON sales_order_shipments USING gin (search_text gin_trgm_ops);

-- === receive_payments ===
ALTER TABLE receive_payments ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_receive_payments() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.payment_number, NEW.customer_name, NEW.reference_number, NEW.notes, NEW.bank_account_name, NEW.journal_number)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_receive_payments ON receive_payments;
CREATE TRIGGER trg_search_text_receive_payments BEFORE INSERT OR UPDATE ON receive_payments
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_receive_payments();
UPDATE receive_payments SET search_text = lower(unaccent(concat_ws(' ', payment_number, customer_name, reference_number, notes, bank_account_name, journal_number)));
CREATE INDEX IF NOT EXISTS idx_receive_payments_search_trgm ON receive_payments USING gin (search_text gin_trgm_ops);

-- === customer_deposits ===
ALTER TABLE customer_deposits ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_customer_deposits() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.deposit_number, NEW.customer_name, NEW.reference, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_customer_deposits ON customer_deposits;
CREATE TRIGGER trg_search_text_customer_deposits BEFORE INSERT OR UPDATE ON customer_deposits
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_customer_deposits();
UPDATE customer_deposits SET search_text = lower(unaccent(concat_ws(' ', deposit_number, customer_name, reference, notes)));
CREATE INDEX IF NOT EXISTS idx_customer_deposits_search_trgm ON customer_deposits USING gin (search_text gin_trgm_ops);

-- === credit_notes ===
ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS search_text text;
CREATE OR REPLACE FUNCTION fn_search_text_credit_notes() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.credit_note_number, NEW.customer_name, NEW.original_invoice_number, NEW.reason, NEW.reason_detail, NEW.ref_no, NEW.notes)));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_search_text_credit_notes ON credit_notes;
CREATE TRIGGER trg_search_text_credit_notes BEFORE INSERT OR UPDATE ON credit_notes
  FOR EACH ROW EXECUTE FUNCTION fn_search_text_credit_notes();
UPDATE credit_notes SET search_text = lower(unaccent(concat_ws(' ', credit_note_number, customer_name, original_invoice_number, reason, reason_detail, ref_no, notes)));
CREATE INDEX IF NOT EXISTS idx_credit_notes_search_trgm ON credit_notes USING gin (search_text gin_trgm_ops);

COMMIT;
