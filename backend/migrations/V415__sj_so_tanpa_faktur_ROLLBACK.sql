-- Rollback V415. -v ON_ERROR_STOP=1 -1. Menolak bila sudah ada SJ-so atau alokasi (fakta dokumen).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM invoice_fulfillments WHERE asal <> 'faktur') OR EXISTS (SELECT 1 FROM sj_faktur_alokasi) THEN
        RAISE EXCEPTION 'ROLLBACK V415 ditolak: ada SJ-so atau alokasi';
    END IF;
END $$;
DROP TRIGGER IF EXISTS trg_sjfa_predikat ON sj_faktur_alokasi;
DROP FUNCTION IF EXISTS trg_sjfa_predikat();
DROP TABLE IF EXISTS sj_faktur_alokasi;
DROP INDEX IF EXISTS idx_ifi_soi;
ALTER TABLE invoice_fulfillment_items DROP CONSTRAINT IF EXISTS chk_ifi_tautan;
ALTER TABLE invoice_fulfillment_items DROP COLUMN IF EXISTS sales_order_item_id;
ALTER TABLE invoice_fulfillment_items ALTER COLUMN invoice_item_id SET NOT NULL;
DROP INDEX IF EXISTS idx_if_so;
ALTER TABLE invoice_fulfillments DROP CONSTRAINT IF EXISTS chk_if_asal_kolom;
ALTER TABLE invoice_fulfillments DROP CONSTRAINT IF EXISTS chk_if_asal;
ALTER TABLE invoice_fulfillments DROP COLUMN IF EXISTS asal;
ALTER TABLE invoice_fulfillments DROP COLUMN IF EXISTS sales_order_id;
ALTER TABLE invoice_fulfillments ALTER COLUMN invoice_id SET NOT NULL;
-- Fungsi SO: kembalikan ke versi V264/V315 (tanpa cabang SJ-so)
CREATE OR REPLACE FUNCTION trg_so_shipped_from_fulfillment() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE v_so uuid;
BEGIN
    SELECT si.sales_order_id INTO v_so FROM sales_invoices si WHERE si.id = COALESCE(NEW.invoice_id, OLD.invoice_id);
    IF v_so IS NOT NULL THEN PERFORM recompute_so_shipped(v_so); END IF;
    RETURN NULL;
END $f$;
CREATE OR REPLACE FUNCTION trg_so_shipped_from_fulfillment_item() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE v_so uuid;
BEGIN
    SELECT si.sales_order_id INTO v_so FROM invoice_fulfillments f JOIN sales_invoices si ON si.id = f.invoice_id
     WHERE f.id = COALESCE(NEW.fulfillment_id, OLD.fulfillment_id);
    IF v_so IS NOT NULL THEN PERFORM recompute_so_shipped(v_so); END IF;
    RETURN NULL;
END $f$;
CREATE OR REPLACE FUNCTION recompute_so_shipped(p_so_id uuid) RETURNS void LANGUAGE plpgsql AS $f$
DECLARE v_ship numeric; v_inv numeric; v_ordered numeric; v_status text; v_new text;
BEGIN
    SELECT COALESCE(SUM(ifi.quantity), 0) INTO v_ship FROM sales_invoices si
      JOIN invoice_fulfillments f ON f.invoice_id = si.id AND f.voided_at IS NULL AND f.status <> 'voided'
      JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id WHERE si.sales_order_id = p_so_id;
    SELECT so.status, so.invoiced_qty, COALESCE((SELECT SUM(quantity) FROM sales_order_items WHERE sales_order_id = p_so_id), 0)
      INTO v_status, v_inv, v_ordered FROM sales_orders so WHERE so.id = p_so_id;
    IF NOT FOUND THEN RETURN; END IF;
    v_new := compute_so_status(v_inv, v_ship, v_ordered, v_status);
    UPDATE sales_orders SET shipped_qty = v_ship, status = v_new, updated_at = NOW()
     WHERE id = p_so_id AND (shipped_qty IS DISTINCT FROM v_ship OR status IS DISTINCT FROM v_new);
    PERFORM terapkan_selesai_so(p_so_id);
END $f$;
CREATE OR REPLACE FUNCTION so_memenuhi_selesai(p_so uuid) RETURNS boolean LANGUAGE sql STABLE AS $f$
    WITH so AS (SELECT id, tenant_id FROM sales_orders WHERE id = p_so),
    baris AS (
        SELECT soi.id, soi.quantity AS q, COALESCE(soi.perlu_kirim, p.track_inventory, false) AS stok,
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
$f$;
CREATE OR REPLACE FUNCTION verify_so_shipped_reconciliation(p_tenant text)
 RETURNS TABLE(so_id uuid, order_number text, stored_shipped numeric, derived_shipped numeric, status text)
 LANGUAGE sql STABLE AS $f$
  SELECT so.id, so.order_number, so.shipped_qty,
    COALESCE((SELECT SUM(ifi.quantity) FROM sales_invoices si
      JOIN invoice_fulfillments f ON f.invoice_id=si.id AND f.voided_at IS NULL AND f.status<>'voided'
      JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id=f.id
      WHERE si.sales_order_id=so.id),0) AS derived, so.status
  FROM sales_orders so
  WHERE so.tenant_id = p_tenant
    AND so.shipped_qty IS DISTINCT FROM COALESCE((SELECT SUM(ifi.quantity) FROM sales_invoices si
      JOIN invoice_fulfillments f ON f.invoice_id=si.id AND f.voided_at IS NULL AND f.status<>'voided'
      JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id=f.id
      WHERE si.sales_order_id=so.id),0);
$f$;
