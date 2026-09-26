-- V318 (27 Sep 2026, BACKEND; putusan pemilik via MASTER) — Surat Jalan untuk barang NON-STOK "bisa dikirim"
-- (pola NetSuite "Can be Fulfilled" / SAP non-stock NLAG: dikirim TANPA jurnal/mutasi stok).
-- Tanpa BEGIN/COMMIT: apply_mig.sh menjalankan berkas dengan psql -1 (satu transaksi).
--
-- 1) products.bisa_dikirim (default false; JASA tak pernah bisa dikirim — CHECK).
-- 2) SNAPSHOT per baris dokumen: sales_order_items.perlu_kirim + sales_invoice_items.perlu_kirim, diisi TRIGGER
--    BEFORE INSERT / ganti item (DB = satu-satunya chokepoint SEMUA jalur pembuat baris: SO, penawaran->SO,
--    SO->faktur, faktur langsung, chat). Nilai = track_inventory OR bisa_dikirim saat baris dibuat; baris faktur
--    dari SO MEWARISI snapshot baris SO-nya. Mengubah flag barang TIDAK mengubah dokumen yang sudah ada.
--    Baris LAMA = NULL -> dibaca aturan lama (track_inventory) di semua pembaca.
-- 3) so_memenuhi_selesai (V315): baris wajib terkirim = COALESCE(soi.perlu_kirim, track_inventory, false).
--    Untuk semua data hari ini (flag false di mana-mana) hasilnya IDENTIK dengan V315.
-- Urutan deploy: MIGRASI DULU (aditif; kode lama tak membaca kolom baru; predikat identik), kode menyusul.
-- Tenant (Law 24): produk dicari lewat tenant dokumen induknya, bukan id saja.

ALTER TABLE products ADD COLUMN IF NOT EXISTS bisa_dikirim boolean NOT NULL DEFAULT false;
ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_products_bisa_dikirim_bukan_jasa;
ALTER TABLE products ADD CONSTRAINT chk_products_bisa_dikirim_bukan_jasa
    CHECK (NOT bisa_dikirim OR item_type IS DISTINCT FROM 'service');

ALTER TABLE sales_order_items ADD COLUMN IF NOT EXISTS perlu_kirim boolean;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS perlu_kirim boolean;

CREATE OR REPLACE FUNCTION isi_perlu_kirim_soi() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.item_id IS NOT DISTINCT FROM OLD.item_id THEN
        RETURN NEW;
    END IF;
    IF NEW.item_id IS NULL THEN
        NEW.perlu_kirim := false;
    ELSE
        SELECT COALESCE(p.track_inventory, false) OR COALESCE(p.bisa_dikirim, false) INTO NEW.perlu_kirim
          FROM sales_orders so JOIN products p ON p.id = NEW.item_id AND p.tenant_id = so.tenant_id
         WHERE so.id = NEW.sales_order_id;
        NEW.perlu_kirim := COALESCE(NEW.perlu_kirim, false);
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION isi_perlu_kirim_sii() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v boolean;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.item_id IS NOT DISTINCT FROM OLD.item_id
       AND NEW.sales_order_item_id IS NOT DISTINCT FROM OLD.sales_order_item_id THEN
        RETURN NEW;
    END IF;
    IF NEW.sales_order_item_id IS NOT NULL THEN
        SELECT soi.perlu_kirim INTO v
          FROM sales_order_items soi
          JOIN sales_orders so ON so.id = soi.sales_order_id
          JOIN sales_invoices si ON si.id = NEW.invoice_id AND si.tenant_id = so.tenant_id
         WHERE soi.id = NEW.sales_order_item_id;
    END IF;
    IF v IS NULL THEN
        IF NEW.item_id IS NULL THEN
            v := false;
        ELSE
            SELECT COALESCE(p.track_inventory, false) OR COALESCE(p.bisa_dikirim, false) INTO v
              FROM sales_invoices si JOIN products p ON p.id = NEW.item_id AND p.tenant_id = si.tenant_id
             WHERE si.id = NEW.invoice_id;
        END IF;
    END IF;
    NEW.perlu_kirim := COALESCE(v, false);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_isi_perlu_kirim_soi ON sales_order_items;
CREATE TRIGGER trg_isi_perlu_kirim_soi BEFORE INSERT OR UPDATE OF item_id ON sales_order_items
    FOR EACH ROW EXECUTE FUNCTION isi_perlu_kirim_soi();
DROP TRIGGER IF EXISTS trg_isi_perlu_kirim_sii ON sales_invoice_items;
CREATE TRIGGER trg_isi_perlu_kirim_sii BEFORE INSERT OR UPDATE OF item_id, sales_order_item_id ON sales_invoice_items
    FOR EACH ROW EXECUTE FUNCTION isi_perlu_kirim_sii();

-- 3) predikat V315: satu ekspresi berubah (stok -> wajib kirim menurut snapshot baris SO; NULL = aturan lama)
CREATE OR REPLACE FUNCTION so_memenuhi_selesai(p_so uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
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
$$;
