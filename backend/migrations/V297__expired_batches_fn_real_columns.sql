-- V297: get_expired_batches() referenced products.name, which does not exist (the column is
-- nama_produk) -> "column i.name does not exist" on EVERY call -> GET /api/item-batches/expired
-- answered 500 always (measured 23 Sep 2026 in the bigint-class sweep; the FE BatchExpiry screen,
-- opened from ChatPanel 'batch_kedaluwarsa', calls it).
-- Also: total_value was (quantity * unit_cost)::BIGINT -- money cast to integer drops the sen.
-- Now NUMERIC(18,2), rounded HALF_UP (ROUND). Return type change -> DROP + CREATE.
-- item_name COALESCEd: the product join is a LEFT JOIN and the response model requires a string.
DROP FUNCTION IF EXISTS get_expired_batches(text, uuid);

CREATE FUNCTION get_expired_batches(p_tenant_id text, p_warehouse_id uuid DEFAULT NULL::uuid)
 RETURNS TABLE(batch_id uuid, item_id uuid, item_name character varying, batch_number character varying, expiry_date date, days_expired integer, quantity numeric, total_value numeric(18,2), warehouse_id uuid, warehouse_name character varying)
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        ib.id as batch_id,
        ib.item_id,
        COALESCE(i.nama_produk, '(produk tidak ditemukan)')::character varying as item_name,
        ib.batch_number,
        ib.expiry_date,
        (CURRENT_DATE - ib.expiry_date)::INTEGER as days_expired,
        bws.quantity,
        ROUND(bws.quantity * COALESCE(ib.unit_cost, 0), 2)::numeric(18,2) as total_value,
        bws.warehouse_id,
        w.name as warehouse_name
    FROM item_batches ib
    JOIN batch_warehouse_stock bws ON ib.id = bws.batch_id
    JOIN warehouses w ON bws.warehouse_id = w.id
    LEFT JOIN products i ON ib.item_id = i.id
    WHERE ib.tenant_id = p_tenant_id
    AND ib.expiry_date IS NOT NULL
    AND ib.expiry_date < CURRENT_DATE
    AND bws.quantity > 0
    AND (p_warehouse_id IS NULL OR bws.warehouse_id = p_warehouse_id)
    ORDER BY ib.expiry_date ASC;
END;
$function$;
