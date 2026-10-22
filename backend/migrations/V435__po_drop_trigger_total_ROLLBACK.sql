-- ROLLBACK V435: pasang kembali trigger lama (BIGINT, menimpa total header = Σ baris). Pakai hanya bila kode lama dipulihkan.
CREATE OR REPLACE FUNCTION public.update_purchase_order_totals()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_po_id UUID;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_po_id := OLD.po_id;
    ELSE
        v_po_id := NEW.po_id;
    END IF;

    SELECT
        COALESCE(SUM(subtotal), 0),
        COALESCE(SUM(tax_amount), 0),
        COALESCE(SUM(total), 0)
    INTO v_subtotal, v_tax_amount, v_total
    FROM purchase_order_items
    WHERE po_id = v_po_id;

    UPDATE purchase_orders
    SET subtotal = v_subtotal,
        tax_amount = v_tax_amount,
        total_amount = v_total,
        updated_at = NOW()
    WHERE id = v_po_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;
CREATE TRIGGER trg_update_po_totals AFTER INSERT OR DELETE OR UPDATE ON public.purchase_order_items
    FOR EACH ROW EXECUTE FUNCTION update_purchase_order_totals();
