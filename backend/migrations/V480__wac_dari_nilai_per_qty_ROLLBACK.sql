-- Pembalik V480 (BUG-057): kembalikan get_weighted_average_cost ke definisi SEBELUM V480 (average_cost baris terbaru; disalin dari
-- pg_get_functiondef produksi 6 Nov 2026). Nol data diubah.

CREATE OR REPLACE FUNCTION public.get_weighted_average_cost(p_tenant_id text, p_product_id uuid)
 RETURNS numeric
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_avg_cost NUMERIC;
    v_fallback_cost NUMERIC;
BEGIN
    SELECT average_cost INTO v_avg_cost
    FROM inventory_ledger
    WHERE tenant_id = p_tenant_id
      AND product_id = p_product_id
      AND average_cost IS NOT NULL
    ORDER BY movement_date DESC, created_at DESC
    LIMIT 1;

    IF v_avg_cost IS NOT NULL THEN
        RETURN v_avg_cost;
    END IF;

    -- Fallback to product purchase_price
    SELECT purchase_price INTO v_fallback_cost
    FROM products
    WHERE tenant_id = p_tenant_id AND id = p_product_id;

    RETURN COALESCE(v_fallback_cost, 0);
END;
$function$;
