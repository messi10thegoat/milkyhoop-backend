-- ROLLBACK V264: cabut trigger/fungsi baru, PULIHKAN update_sales_order_status lama.
-- Catatan: DATA shipped_qty/status yang sudah di-backfill TIDAK direset (cache turunan yang BENAR;
-- Rule 8 carve-out). Bila mau reset penuh: UPDATE sales_orders SET shipped_qty=0 lalu sentuh item.
DROP TRIGGER IF EXISTS trg_so_shipped_recompute ON invoice_fulfillments;
DROP TRIGGER IF EXISTS trg_so_shipped_recompute ON invoice_fulfillment_items;
DROP FUNCTION IF EXISTS public.trg_so_shipped_from_fulfillment();
DROP FUNCTION IF EXISTS public.trg_so_shipped_from_fulfillment_item();
DROP FUNCTION IF EXISTS public.recompute_so_shipped(uuid);
DROP FUNCTION IF EXISTS public.verify_so_shipped_reconciliation(text);
DROP FUNCTION IF EXISTS public.compute_so_status(numeric, numeric, numeric, text);

-- Pulihkan update_sales_order_status versi PRA-V264 (shipped_qty dari SUM quantity_shipped; invoiced-first).
CREATE OR REPLACE FUNCTION public.update_sales_order_status() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_so_id UUID; v_total_qty DECIMAL; v_shipped_qty DECIMAL; v_invoiced_qty DECIMAL;
    v_current_status VARCHAR(30); v_new_status VARCHAR(30);
BEGIN
    v_so_id := COALESCE(NEW.sales_order_id, OLD.sales_order_id);
    SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(quantity_shipped),0), COALESCE(SUM(quantity_invoiced),0)
      INTO v_total_qty, v_shipped_qty, v_invoiced_qty FROM sales_order_items WHERE sales_order_id=v_so_id;
    SELECT status INTO v_current_status FROM sales_orders WHERE id=v_so_id;
    IF v_current_status='cancelled' THEN RETURN NEW; END IF;
    IF v_current_status='draft' THEN v_new_status:='draft';
    ELSIF v_invoiced_qty>=v_total_qty THEN v_new_status:='invoiced';
    ELSIF v_invoiced_qty>0 THEN v_new_status:='partial_invoiced';
    ELSIF v_shipped_qty>=v_total_qty THEN v_new_status:='shipped';
    ELSIF v_shipped_qty>0 THEN v_new_status:='partial_shipped';
    ELSE v_new_status:='confirmed'; END IF;
    UPDATE sales_orders SET status=v_new_status, shipped_qty=v_shipped_qty, invoiced_qty=v_invoiced_qty, updated_at=NOW()
     WHERE id=v_so_id AND (status!=v_new_status OR shipped_qty!=v_shipped_qty OR invoiced_qty!=v_invoiced_qty);
    RETURN NEW;
END;
$$;
