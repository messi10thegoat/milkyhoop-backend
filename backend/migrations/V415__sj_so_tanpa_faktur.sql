-- V415 (BACKEND2 12 Okt 2026; R1 IRISAN 1: Surat Jalan dari SO TANPA faktur, barang NON-STOK; putusan MASTER) -- JALANKAN dengan:
--   psql -v ON_ERROR_STOP=1 -1   (satu transaksi)
-- TANPA aturan buku baru: SJ non-stok = dokumen (nol jurnal, nol kartu stok, nol HPP). Pendapatan non-stok tetap diakui saat
-- faktur diposting (jalur lama). Irisan 2 (barang ber-stok + akun Barang Terkirim) TIDAK di sini.
--   1. invoice_fulfillments: invoice_id NULLABLE, + sales_order_id, + asal ('faktur' | 'so'), CHECK kolom sesuai asal.
--   2. invoice_fulfillment_items: invoice_item_id NULLABLE, + sales_order_item_id, CHECK minimal satu tautan.
--   3. sj_faktur_alokasi (INSERT-only; hanya voided_*): satu baris SJ-so <-> baris faktur; predikat DB: alokasi aktif tak melebihi
--      qty baris SJ maupun qty baris faktur (invarian atas baris di DB, bukan konvensi kode).
--   4. Fungsi/trigger SO yang menghubungkan SJ ke SO LEWAT faktur kini mengenal SJ-so:
--      trg_so_shipped_from_fulfillment(_item), recompute_so_shipped, so_memenuhi_selesai, verify_so_shipped_reconciliation.
-- SJ lama otomatis asal='faktur' (DEFAULT); nol baris data diubah. Harness: hitung ulang semua SO prod identik.
ALTER TABLE invoice_fulfillments ALTER COLUMN invoice_id DROP NOT NULL;
ALTER TABLE invoice_fulfillments ADD COLUMN IF NOT EXISTS sales_order_id uuid REFERENCES sales_orders(id);
ALTER TABLE invoice_fulfillments ADD COLUMN IF NOT EXISTS asal text NOT NULL DEFAULT 'faktur';
ALTER TABLE invoice_fulfillments DROP CONSTRAINT IF EXISTS chk_if_asal;
ALTER TABLE invoice_fulfillments ADD CONSTRAINT chk_if_asal CHECK (asal IN ('faktur', 'so'));
ALTER TABLE invoice_fulfillments DROP CONSTRAINT IF EXISTS chk_if_asal_kolom;
ALTER TABLE invoice_fulfillments ADD CONSTRAINT chk_if_asal_kolom CHECK (
    (asal = 'faktur' AND invoice_id IS NOT NULL)
 OR (asal = 'so' AND invoice_id IS NULL AND sales_order_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS idx_if_so ON invoice_fulfillments (tenant_id, sales_order_id) WHERE sales_order_id IS NOT NULL;

ALTER TABLE invoice_fulfillment_items ALTER COLUMN invoice_item_id DROP NOT NULL;
ALTER TABLE invoice_fulfillment_items ADD COLUMN IF NOT EXISTS sales_order_item_id uuid REFERENCES sales_order_items(id);
ALTER TABLE invoice_fulfillment_items DROP CONSTRAINT IF EXISTS chk_ifi_tautan;
ALTER TABLE invoice_fulfillment_items ADD CONSTRAINT chk_ifi_tautan CHECK (invoice_item_id IS NOT NULL OR sales_order_item_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_ifi_soi ON invoice_fulfillment_items (sales_order_item_id) WHERE sales_order_item_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sj_faktur_alokasi (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           text NOT NULL,
    fulfillment_item_id uuid NOT NULL REFERENCES invoice_fulfillment_items(id),
    invoice_item_id     uuid NOT NULL REFERENCES sales_invoice_items(id),
    quantity            numeric NOT NULL CHECK (quantity > 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid,
    voided_at           timestamptz,
    voided_reason       text
);
CREATE INDEX IF NOT EXISTS idx_sjfa_ifi ON sj_faktur_alokasi (fulfillment_item_id) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sjfa_sii ON sj_faktur_alokasi (invoice_item_id) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sjfa_tenant ON sj_faktur_alokasi (tenant_id);

CREATE OR REPLACE FUNCTION trg_sjfa_predikat() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE v_qty_sj numeric; v_qty_faktur numeric; v_pakai_sj numeric; v_pakai_faktur numeric;
BEGIN
    IF NEW.voided_at IS NOT NULL THEN RETURN NEW; END IF;
    SELECT quantity INTO v_qty_sj FROM invoice_fulfillment_items WHERE id = NEW.fulfillment_item_id;
    SELECT quantity INTO v_qty_faktur FROM sales_invoice_items WHERE id = NEW.invoice_item_id;
    SELECT COALESCE(SUM(quantity), 0) INTO v_pakai_sj FROM sj_faktur_alokasi
     WHERE fulfillment_item_id = NEW.fulfillment_item_id AND voided_at IS NULL AND id <> NEW.id;
    SELECT COALESCE(SUM(quantity), 0) INTO v_pakai_faktur FROM sj_faktur_alokasi
     WHERE invoice_item_id = NEW.invoice_item_id AND voided_at IS NULL AND id <> NEW.id;
    IF v_pakai_sj + NEW.quantity > v_qty_sj THEN
        RAISE EXCEPTION 'sj_faktur_alokasi: alokasi % melebihi qty baris Surat Jalan % (terpakai %)', NEW.quantity, v_qty_sj, v_pakai_sj;
    END IF;
    IF v_pakai_faktur + NEW.quantity > v_qty_faktur THEN
        RAISE EXCEPTION 'sj_faktur_alokasi: alokasi % melebihi qty baris faktur % (terpakai %)', NEW.quantity, v_qty_faktur, v_pakai_faktur;
    END IF;
    RETURN NEW;
END $f$;
DROP TRIGGER IF EXISTS trg_sjfa_predikat ON sj_faktur_alokasi;
CREATE TRIGGER trg_sjfa_predikat BEFORE INSERT ON sj_faktur_alokasi FOR EACH ROW EXECUTE FUNCTION trg_sjfa_predikat();

CREATE OR REPLACE FUNCTION trg_so_shipped_from_fulfillment() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE v_so uuid;
BEGIN
    v_so := COALESCE(NEW.sales_order_id, OLD.sales_order_id);
    IF v_so IS NULL THEN
        SELECT si.sales_order_id INTO v_so FROM sales_invoices si
         WHERE si.id = COALESCE(NEW.invoice_id, OLD.invoice_id);
    END IF;
    IF v_so IS NOT NULL THEN PERFORM recompute_so_shipped(v_so); END IF;
    RETURN NULL;
END $f$;

CREATE OR REPLACE FUNCTION trg_so_shipped_from_fulfillment_item() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE v_so uuid;
BEGIN
    SELECT COALESCE(f.sales_order_id, si.sales_order_id) INTO v_so
      FROM invoice_fulfillments f LEFT JOIN sales_invoices si ON si.id = f.invoice_id
     WHERE f.id = COALESCE(NEW.fulfillment_id, OLD.fulfillment_id);
    IF v_so IS NOT NULL THEN PERFORM recompute_so_shipped(v_so); END IF;
    RETURN NULL;
END $f$;

CREATE OR REPLACE FUNCTION recompute_so_shipped(p_so_id uuid) RETURNS void LANGUAGE plpgsql AS $f$
DECLARE v_ship numeric; v_inv numeric; v_ordered numeric; v_status text; v_new text;
BEGIN
    SELECT COALESCE(SUM(q), 0) INTO v_ship FROM (
        SELECT ifi.quantity AS q
          FROM sales_invoices si
          JOIN invoice_fulfillments f ON f.invoice_id = si.id AND f.voided_at IS NULL AND f.status <> 'voided'
          JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
         WHERE si.sales_order_id = p_so_id
        UNION ALL
        SELECT ifi.quantity
          FROM invoice_fulfillments f
          JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
         WHERE f.asal = 'so' AND f.sales_order_id = p_so_id AND f.voided_at IS NULL AND f.status <> 'voided'
    ) x;
    SELECT so.status, so.invoiced_qty,
           COALESCE((SELECT SUM(quantity) FROM sales_order_items WHERE sales_order_id = p_so_id), 0)
      INTO v_status, v_inv, v_ordered
      FROM sales_orders so WHERE so.id = p_so_id;
    IF NOT FOUND THEN RETURN; END IF;
    v_new := compute_so_status(v_inv, v_ship, v_ordered, v_status);
    UPDATE sales_orders SET shipped_qty = v_ship, status = v_new, updated_at = NOW()
     WHERE id = p_so_id AND (shipped_qty IS DISTINCT FROM v_ship OR status IS DISTINCT FROM v_new);
    PERFORM terapkan_selesai_so(p_so_id);  -- V315
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
                         WHERE sii.sales_order_item_id = soi.id), 0)
             + COALESCE((SELECT SUM(ifi.quantity) FROM invoice_fulfillment_items ifi
                          JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.asal = 'so'
                               AND f.tenant_id = so.tenant_id AND f.voided_at IS NULL AND f.status <> 'voided'
                         WHERE ifi.sales_order_item_id = soi.id), 0) AS kirim
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
  SELECT so.id, so.order_number, so.shipped_qty, d.derived, so.status
  FROM sales_orders so
  CROSS JOIN LATERAL (
    SELECT COALESCE(SUM(q), 0) AS derived FROM (
      SELECT ifi.quantity AS q FROM sales_invoices si
        JOIN invoice_fulfillments f ON f.invoice_id = si.id AND f.voided_at IS NULL AND f.status <> 'voided'
        JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
       WHERE si.sales_order_id = so.id
      UNION ALL
      SELECT ifi.quantity FROM invoice_fulfillments f
        JOIN invoice_fulfillment_items ifi ON ifi.fulfillment_id = f.id
       WHERE f.asal = 'so' AND f.sales_order_id = so.id AND f.voided_at IS NULL AND f.status <> 'voided'
    ) x
  ) d
  WHERE so.tenant_id = p_tenant AND so.shipped_qty IS DISTINCT FROM d.derived;
$f$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'invoice_fulfillments' AND column_name = 'asal') THEN
        RAISE EXCEPTION 'V415: kolom asal tidak terpasang';
    END IF;
    IF EXISTS (SELECT 1 FROM invoice_fulfillments WHERE asal <> 'faktur') THEN
        RAISE EXCEPTION 'V415: ada SJ non-faktur sebelum kode jalur-nya hidup';
    END IF;
END $$;
