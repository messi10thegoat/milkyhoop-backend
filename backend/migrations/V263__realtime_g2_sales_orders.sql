-- V263: Realtime Tahap 2 G2 — trigger doc_changed di sales_orders (HEADER saja).
-- SO ter-cover PENUH lewat header: (a) status confirm/cancel/complete + edit -> UPDATE sales_orders;
-- (b) perubahan item & convert-to-invoice (quantity_invoiced) PROPAGASI ke header via trigger eksisting
-- trg_update_so_status (AFTER UPDATE sales_order_items -> UPDATE sales_orders status/shipped_qty/
-- invoiced_qty/updated_at). sales_order_shipments TAK dipakai (0 baris, 0 INSERT). => TAK perlu
-- trigger aplikasi. Fungsi notify_doc_changed() generik (V259). Modul SALES_ORDER.
DROP TRIGGER IF EXISTS trg_notify_doc_changed ON sales_orders;
CREATE TRIGGER trg_notify_doc_changed
  AFTER INSERT OR UPDATE OR DELETE ON sales_orders
  FOR EACH ROW EXECUTE FUNCTION notify_doc_changed();
