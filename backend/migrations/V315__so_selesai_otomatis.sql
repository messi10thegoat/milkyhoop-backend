-- V315 — SO SELESAI OTOMATIS (26 Sep 2026, BACKEND3; putusan pemilik via MASTER, pola SaaS "Closed" ala Zoho)
-- Tanpa BEGIN/COMMIT: apply_mig.sh menjalankan berkas dengan psql -1 (satu transaksi).
--
-- Aturan: SO -> 'completed' OTOMATIS bila (1) SETIAP baris tertagih penuh pada faktur POSTED non-void,
-- (2) SETIAP baris STOK (products.track_inventory) terkirim penuh lewat Surat Jalan aktif, dan (3) TAK ADA
-- baris faktur SO yang pendapatannya belum diakui (allocated - recognized > 0.005; definisi SAMA dengan
-- /close F1 dan Check 16). Pembayaran BUKAN syarat (dilacak di faktur). Baris non-stok/jasa/teks bebas
-- dianggap terpenuhi saat difakturkan.
-- completed_source: 'manual' (/close, TERMINAL) | 'auto' (aturan ini, DIBUKA KEMBALI bila syarat runtuh —
-- faktur/Surat Jalan dibatalkan). SO 'completed' yang sudah ada = hasil /close -> 'manual' (definisi, bukan
-- perubahan status). Setiap perubahan otomatis dicatat di audit_logs (Law 12), dibaca Riwayat SO.
-- TANPA backfill: SO lama berubah hanya pada kejadian berikutnya; fungsi backfill disediakan TERPISAH
-- (so_selesai_otomatis_backfill) dan hanya dijalankan atas putusan pemilik.


ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS completed_source text;
ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS chk_so_completed_source;
ALTER TABLE sales_orders ADD CONSTRAINT chk_so_completed_source
    CHECK (completed_source IS NULL OR completed_source IN ('auto', 'manual'));
UPDATE sales_orders SET completed_source = 'manual' WHERE status = 'completed' AND completed_source IS NULL;

-- Predikat murni (STABLE): SO memenuhi syarat selesai otomatis?
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

-- Terapkan: selesaikan otomatis / buka kembali yang otomatis. Manual & draft/batal TIDAK disentuh.
CREATE OR REPLACE FUNCTION terapkan_selesai_so(p_so uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE r record; v_ok boolean; v_ordered numeric; v_baru text;
BEGIN
    SELECT id, tenant_id, order_number, status, completed_source, invoiced_qty, shipped_qty
      INTO r FROM sales_orders WHERE id = p_so FOR UPDATE;
    IF NOT FOUND OR r.status IN ('draft', 'cancelled') THEN RETURN NULL; END IF;
    IF r.status = 'completed' AND r.completed_source IS DISTINCT FROM 'auto' THEN RETURN NULL; END IF;  -- manual = terminal
    v_ok := so_memenuhi_selesai(p_so);
    IF v_ok AND r.status <> 'completed' THEN
        UPDATE sales_orders SET status = 'completed', completed_source = 'auto', updated_at = NOW() WHERE id = p_so;
        INSERT INTO audit_logs ("eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
        VALUES ('SALES_ORDER_AUTO_COMPLETED', 'sales_orders', p_so, r.order_number, r.tenant_id, 'db:terapkan_selesai_so',
                jsonb_build_object('ringkas', 'Pesanan ' || r.order_number || ' selesai otomatis (tertagih penuh, barang stok terkirim, pendapatan diakui)',
                                   'dari_status', r.status), true, NOW());
        RETURN 'completed';
    ELSIF NOT v_ok AND r.status = 'completed' AND r.completed_source = 'auto' THEN
        SELECT COALESCE(SUM(quantity), 0) INTO v_ordered FROM sales_order_items WHERE sales_order_id = p_so;
        v_baru := compute_so_status(COALESCE(r.invoiced_qty, 0), COALESCE(r.shipped_qty, 0), v_ordered, 'confirmed');
        UPDATE sales_orders SET status = v_baru, completed_source = NULL, updated_at = NOW() WHERE id = p_so;
        INSERT INTO audit_logs ("eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
        VALUES ('SALES_ORDER_REOPENED', 'sales_orders', p_so, r.order_number, r.tenant_id, 'db:terapkan_selesai_so',
                jsonb_build_object('ringkas', 'Pesanan ' || r.order_number || ' dibuka kembali (' || v_baru || ') — faktur/Surat Jalan dibatalkan',
                                   'ke_status', v_baru), true, NOW());
        RETURN v_baru;
    END IF;
    RETURN NULL;
END;
$$;

-- Dua jalur hitung status yang ADA memanggilnya di akhir (isi lama TAK berubah selain baris PERFORM).
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
    PERFORM terapkan_selesai_so(v_so);  -- V315
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
    PERFORM terapkan_selesai_so(p_so_id);  -- V315
END;
$function$;

-- Kejadian yang DULU tak memicu hitung ulang: faktur diposting/dibatalkan, pendapatan diakui.
CREATE OR REPLACE FUNCTION trg_so_selesai_dari_faktur() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sales_order_id IS NOT NULL THEN PERFORM terapkan_selesai_so(NEW.sales_order_id); END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS trg_so_selesai_faktur ON sales_invoices;
CREATE TRIGGER trg_so_selesai_faktur AFTER UPDATE OF status ON sales_invoices
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION trg_so_selesai_dari_faktur();

CREATE OR REPLACE FUNCTION trg_so_selesai_dari_pengakuan() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_so uuid;
BEGIN
    SELECT sales_order_id INTO v_so FROM sales_invoices WHERE id = NEW.invoice_id;
    IF v_so IS NOT NULL THEN PERFORM terapkan_selesai_so(v_so); END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS trg_so_selesai_pengakuan ON sales_invoice_items;
CREATE TRIGGER trg_so_selesai_pengakuan AFTER UPDATE OF recognized_amount ON sales_invoice_items
    FOR EACH ROW WHEN (OLD.recognized_amount IS DISTINCT FROM NEW.recognized_amount) EXECUTE FUNCTION trg_so_selesai_dari_pengakuan();

-- Backfill SEKALI-JALAN — HANYA atas putusan pemilik. p_jalankan=false = hitung saja (dry-run).
CREATE OR REPLACE FUNCTION so_selesai_otomatis_backfill(p_tenant text, p_jalankan boolean DEFAULT false)
RETURNS TABLE(so_id uuid, order_number text, status_lama text, hasil text)
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
    FOR r IN SELECT so.id, so.order_number, so.status FROM sales_orders so
              WHERE so.tenant_id = p_tenant AND so.status NOT IN ('draft', 'cancelled', 'completed')
              ORDER BY so.order_number LOOP
        IF so_memenuhi_selesai(r.id) THEN
            so_id := r.id; order_number := r.order_number; status_lama := r.status;
            hasil := CASE WHEN p_jalankan THEN terapkan_selesai_so(r.id) ELSE 'AKAN completed (dry-run)' END;
            RETURN NEXT;
        END IF;
    END LOOP;
END; $$;

