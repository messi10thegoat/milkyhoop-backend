-- Pembalik V484 (BUG-099(b)): kembalikan get_inventory_balance (snapshot quantity_balance baris terbaru) dan calculate_weighted_average
-- ke definisi SEBELUM V484 (disalin dari pg_get_functiondef produksi 7 Nov 2026). Nol data diubah.

CREATE OR REPLACE FUNCTION public.get_inventory_balance(p_tenant_id text, p_product_id uuid)
 RETURNS numeric
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_balance DECIMAL;
BEGIN
    SELECT quantity_balance INTO v_balance
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id AND product_id = p_product_id
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    RETURN COALESCE(v_balance, 0);
END;
$function$;

CREATE OR REPLACE FUNCTION public.calculate_weighted_average(p_tenant_id text, p_product_id uuid, p_new_quantity numeric, p_new_unit_cost numeric)
 RETURNS numeric
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_current_balance DECIMAL;
    v_current_avg_cost NUMERIC;
    v_new_avg_cost NUMERIC;
BEGIN
    SELECT quantity_balance, COALESCE(average_cost, 0)
    INTO v_current_balance, v_current_avg_cost
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id AND product_id = p_product_id
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    IF v_current_balance IS NULL THEN
        v_current_balance := 0;
        v_current_avg_cost := 0;
    END IF;

    IF (v_current_balance + p_new_quantity) > 0 THEN
        v_new_avg_cost := (
            (v_current_balance * v_current_avg_cost) + (p_new_quantity * p_new_unit_cost)
        ) / (v_current_balance + p_new_quantity);
    ELSE
        v_new_avg_cost := p_new_unit_cost;
    END IF;

    RETURN v_new_avg_cost;
END;
$function$;
