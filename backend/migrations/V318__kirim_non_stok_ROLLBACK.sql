-- ROLLBACK V318: kembalikan predikat V315 persis, lepas trigger. Kolom DIBIARKAN (data snapshot tak dibuang;
-- kode lama tak membacanya). Jalankan HANYA bila kode V318 sudah ditarik dari gateway.
DROP TRIGGER IF EXISTS trg_isi_perlu_kirim_soi ON sales_order_items;
DROP TRIGGER IF EXISTS trg_isi_perlu_kirim_sii ON sales_invoice_items;
DROP FUNCTION IF EXISTS isi_perlu_kirim_soi();
DROP FUNCTION IF EXISTS isi_perlu_kirim_sii();

CREATE OR REPLACE FUNCTION so_memenuhi_selesai(p_so uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    WITH so AS (SELECT id, tenant_id FROM sales_orders WHERE id = p_so),
    baris AS (
        SELECT soi.id, soi.quantity AS q, COALESCE(p.track_inventory, false) AS stok,
               COALESCE((SELECT SUM(sii.quantity) FROM sales_invoice_items sii
                          JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = so.tenant_id
                         WHERE sii.sales_order_item_id = soi.id AND si.status NOT IN ('draft', 'void')), 0) AS tagih,
               COALESCE((SELECT SUM(ifi.quantity) FROM sales_invoice_items sii
                          JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = so.tenant_id
                          JOIN invoice_fulfillment_items ifi ON ifi.invoice_item_id = sii.id
                          JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id
                               AND f.voided_at IS NULL AND f.status <> 'voided'
                         WHERE sii.sales_order_item_id = soi.id), 0) AS kirim
          FROM so JOIN sales_order_items soi ON soi.sales_order_id = so.id
          LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
    )
    SELECT EXISTS (SELECT 1 FROM baris)
       AND NOT EXISTS (SELECT 1 FROM baris WHERE tagih < q OR (stok AND kirim < q))
       AND NOT EXISTS (
            SELECT 1 FROM so JOIN sales_invoices si ON si.sales_order_id = so.id AND si.tenant_id = so.tenant_id
              JOIN sales_invoice_items sii ON sii.invoice_id = si.id
             WHERE si.status NOT IN ('draft', 'void')
               AND COALESCE(sii.allocated_amount, 0) - COALESCE(sii.recognized_amount, 0) > 0.005)
$$;
