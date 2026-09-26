-- ROLLBACK V315: kembalikan dua fungsi hitung status ke isi pra-V315, lepas trigger & fungsi baru.
-- Kolom completed_source DIPERTAHANKAN (nilai 'auto' = jejak; SO 'completed' auto tetap completed, jadi terminal
-- lagi seperti sebelum V315). Hapus kolom hanya dengan putusan terpisah.
DROP TRIGGER IF EXISTS trg_so_selesai_faktur ON sales_invoices;
DROP TRIGGER IF EXISTS trg_so_selesai_pengakuan ON sales_invoice_items;
CREATE OR REPLACE FUNCTION public.update_sales_order_status()
 RETURNS trigger LANGUAGE plpgsql AS $function$
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
$function$;
CREATE OR REPLACE FUNCTION public.recompute_so_shipped(p_so_id uuid)
 RETURNS void LANGUAGE plpgsql AS $function$
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
$function$;
DROP FUNCTION IF EXISTS trg_so_selesai_dari_faktur();
DROP FUNCTION IF EXISTS trg_so_selesai_dari_pengakuan();
DROP FUNCTION IF EXISTS so_selesai_otomatis_backfill(text, boolean);
DROP FUNCTION IF EXISTS terapkan_selesai_so(uuid);
DROP FUNCTION IF EXISTS so_memenuhi_selesai(uuid);
