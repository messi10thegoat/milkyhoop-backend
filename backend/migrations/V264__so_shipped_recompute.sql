-- V264: shipped_qty SO dari invoice_fulfillments (K2). Matriks status SHIP-first, completed manual.
-- Satu fungsi status bersama; recompute PENUH idempoten; rekonsiliasi trg_update_so_status.

-- 1) Fungsi status BERSAMA (dipanggil KEDUA trigger). completed/cancelled/draft = terminal utk STATUS.
CREATE OR REPLACE FUNCTION public.compute_so_status(
    p_inv numeric, p_ship numeric, p_ordered numeric, p_status_now text
) RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_status_now IN ('draft','cancelled','completed') THEN
        RETURN p_status_now;                       -- terminal/manual: jangan ubah otomatis
    END IF;
    IF p_ordered > 0 AND p_ship >= p_ordered THEN RETURN 'shipped';
    ELSIF p_ship > 0 THEN RETURN 'partial_shipped';
    ELSIF p_ordered > 0 AND p_inv >= p_ordered THEN RETURN 'invoiced';
    ELSIF p_inv > 0 THEN RETURN 'partial_invoiced';
    ELSE RETURN 'confirmed';
    END IF;
END;
$$;

-- 2) recompute_so_shipped: Σ fulfillment_items AKTIF (bukan void) per SO -> UPDATE shipped_qty + status.
--    RECOMPUTE PENUH (tak increment) -> idempoten; guard IS DISTINCT -> no-op saat sama (tak bump/tak event).
CREATE OR REPLACE FUNCTION public.recompute_so_shipped(p_so_id uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_ship numeric; v_inv numeric; v_ordered numeric; v_status text; v_new text;
BEGIN
    SELECT COALESCE(SUM(ifi.quantity),0) INTO v_ship
      FROM sales_invoices si
      JOIN invoice_fulfillments f ON f.invoice_id=si.id AND f.voided_at IS NULL AND f.status <> 'voided'
      JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id=f.id
     WHERE si.sales_order_id = p_so_id;
    SELECT so.status, so.invoiced_qty,
           COALESCE((SELECT SUM(quantity) FROM sales_order_items WHERE sales_order_id=p_so_id),0)
      INTO v_status, v_inv, v_ordered
      FROM sales_orders so WHERE so.id = p_so_id;
    IF NOT FOUND THEN RETURN; END IF;
    v_new := compute_so_status(v_inv, v_ship, v_ordered, v_status);
    UPDATE sales_orders SET shipped_qty=v_ship, status=v_new, updated_at=NOW()
     WHERE id=p_so_id AND (shipped_qty IS DISTINCT FROM v_ship OR status IS DISTINCT FROM v_new);
END;
$$;

-- 3) REKONSILIASI: update_sales_order_status (trigger di sales_order_items) TAK lagi menurunkan
--    shipped_qty dari quantity_shipped (mati/selalu 0 -> dulu menimpa nilai fulfillment). Kini:
--    invoiced_qty dari items, shipped_qty DIBACA terkini (dimiliki recompute_so_shipped), status via
--    fungsi bersama. Kedua trigger konvergen ke compute_so_status, tak berkelahi.
CREATE OR REPLACE FUNCTION public.update_sales_order_status() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_so uuid; v_ordered numeric; v_inv numeric; v_ship numeric; v_status text; v_new text;
BEGIN
    v_so := COALESCE(NEW.sales_order_id, OLD.sales_order_id);
    SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(quantity_invoiced),0)
      INTO v_ordered, v_inv FROM sales_order_items WHERE sales_order_id=v_so;
    SELECT so.status, so.shipped_qty INTO v_status, v_ship FROM sales_orders so WHERE so.id=v_so;
    IF NOT FOUND THEN RETURN COALESCE(NEW, OLD); END IF;
    v_new := compute_so_status(v_inv, v_ship, v_ordered, v_status);
    UPDATE sales_orders SET invoiced_qty=v_inv, status=v_new, updated_at=NOW()
     WHERE id=v_so AND (invoiced_qty IS DISTINCT FROM v_inv OR status IS DISTINCT FROM v_new);
    RETURN COALESCE(NEW, OLD);
END;
$$;

-- 4) Trigger fns fulfillment -> recompute SO induk (resolve via invoice.sales_order_id).
CREATE OR REPLACE FUNCTION public.trg_so_shipped_from_fulfillment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_so uuid;
BEGIN
    SELECT si.sales_order_id INTO v_so FROM sales_invoices si
     WHERE si.id = COALESCE(NEW.invoice_id, OLD.invoice_id);
    IF v_so IS NOT NULL THEN PERFORM recompute_so_shipped(v_so); END IF;
    RETURN NULL;
END;
$$;
CREATE OR REPLACE FUNCTION public.trg_so_shipped_from_fulfillment_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_so uuid;
BEGIN
    SELECT si.sales_order_id INTO v_so
      FROM invoice_fulfillments f JOIN sales_invoices si ON si.id=f.invoice_id
     WHERE f.id = COALESCE(NEW.fulfillment_id, OLD.fulfillment_id);
    IF v_so IS NOT NULL THEN PERFORM recompute_so_shipped(v_so); END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_so_shipped_recompute ON invoice_fulfillments;
CREATE TRIGGER trg_so_shipped_recompute
  AFTER INSERT OR UPDATE OR DELETE ON invoice_fulfillments
  FOR EACH ROW EXECUTE FUNCTION trg_so_shipped_from_fulfillment();
DROP TRIGGER IF EXISTS trg_so_shipped_recompute ON invoice_fulfillment_items;
CREATE TRIGGER trg_so_shipped_recompute
  AFTER INSERT OR UPDATE OR DELETE ON invoice_fulfillment_items
  FOR EACH ROW EXECUTE FUNCTION trg_so_shipped_from_fulfillment_item();

-- 5) Law 13 checker: stored shipped_qty == derived (0 baris = sehat). Status completed dgn shipped<ordered
--    BUKAN pelanggaran (info) -> tak dimasukkan sini.
CREATE OR REPLACE FUNCTION public.verify_so_shipped_reconciliation(p_tenant text)
RETURNS TABLE(so_id uuid, order_number text, stored_shipped numeric, derived_shipped numeric, status text)
LANGUAGE sql STABLE AS $$
  SELECT so.id, so.order_number, so.shipped_qty,
    COALESCE((SELECT SUM(ifi.quantity) FROM sales_invoices si
      JOIN invoice_fulfillments f ON f.invoice_id=si.id AND f.voided_at IS NULL AND f.status<>'voided'
      JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id=f.id
      WHERE si.sales_order_id=so.id),0) AS derived,
    so.status
  FROM sales_orders so
  WHERE so.tenant_id = p_tenant
    AND so.shipped_qty IS DISTINCT FROM COALESCE((SELECT SUM(ifi.quantity) FROM sales_invoices si
      JOIN invoice_fulfillments f ON f.invoice_id=si.id AND f.voided_at IS NULL AND f.status<>'voided'
      JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id=f.id
      WHERE si.sales_order_id=so.id),0);
$$;

-- 6) D) BACKFILL: recompute semua SO (idempoten; hanya yg berubah ter-UPDATE). Menerapkan shipped_qty
--    + 2 perubahan status (dry-run: SO-2609-0007 & -0012 invoiced->shipped). completed dipertahankan.
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT id FROM sales_orders LOOP PERFORM recompute_so_shipped(r.id); END LOOP;
END $$;
