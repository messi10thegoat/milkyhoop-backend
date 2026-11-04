-- V476 ROLLBACK: pulihkan tabel customer_price_lists (DDL V028) + fungsi get_item_price (definisi V306). Tabel kembali KOSONG
-- (V476 menolak membuang tabel berisi).
CREATE TABLE IF NOT EXISTS customer_price_lists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    price_list_id UUID NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
    priority INT DEFAULT 100,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_customer_price_list UNIQUE(customer_id, price_list_id)
);
CREATE INDEX IF NOT EXISTS idx_customer_price_lists_customer ON customer_price_lists(customer_id);
ALTER TABLE customer_price_lists ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_customer_price_lists ON customer_price_lists;
CREATE POLICY rls_customer_price_lists ON customer_price_lists
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- get_item_price: 4x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_item_price(p_tenant_id text, p_item_id uuid, p_customer_id uuid DEFAULT NULL::uuid, p_quantity numeric DEFAULT 1, p_unit character varying DEFAULT NULL::character varying)
 RETURNS bigint
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_price BIGINT;
BEGIN
    -- First try customer-specific price list
    IF p_customer_id IS NOT NULL THEN
        SELECT pli.price INTO v_price
        FROM price_list_items pli
        JOIN price_lists pl ON pli.price_list_id = pl.id
        JOIN customer_price_lists cpl ON pl.id = cpl.price_list_id
        WHERE pl.tenant_id = p_tenant_id
          AND cpl.customer_id = p_customer_id
          AND cpl.is_active = true
          AND pli.item_id = p_item_id
          AND pli.is_active = true
          AND pl.is_active = true
          AND (pli.unit = p_unit OR pli.unit IS NULL OR p_unit IS NULL)
          AND pli.min_quantity <= p_quantity
          AND (pl.start_date IS NULL OR pl.start_date <= tanggal_bisnis(p_tenant_id))
          AND (pl.end_date IS NULL OR pl.end_date >= tanggal_bisnis(p_tenant_id))
        ORDER BY cpl.priority ASC, pli.min_quantity DESC
        LIMIT 1;

        IF v_price IS NOT NULL THEN
            RETURN v_price;
        END IF;
    END IF;

    -- Fall back to default price list
    SELECT pli.price INTO v_price
    FROM price_list_items pli
    JOIN price_lists pl ON pli.price_list_id = pl.id
    WHERE pl.tenant_id = p_tenant_id
      AND pl.is_default = true
      AND pl.is_active = true
      AND pli.item_id = p_item_id
      AND pli.is_active = true
      AND (pli.unit = p_unit OR pli.unit IS NULL OR p_unit IS NULL)
      AND pli.min_quantity <= p_quantity
      AND (pl.start_date IS NULL OR pl.start_date <= tanggal_bisnis(p_tenant_id))
      AND (pl.end_date IS NULL OR pl.end_date >= tanggal_bisnis(p_tenant_id))
    ORDER BY pli.min_quantity DESC
    LIMIT 1;

    RETURN v_price;
END;
$function$
;
