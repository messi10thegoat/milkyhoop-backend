-- V308 (#30, 25 Sep 2026) — tautan lampiran hub ikut lepas saat entitasnya DIHAPUS-KERAS.
--
-- Terukur 25 Sep (baca-saja): document_attachments.entity_id polimorf TANPA FK -> hapus draf
-- meninggalkan tautan yatim: expense kaos 14 + grapgrap 4. Rute modul menghapus entitas di ~20
-- jalur (draf faktur/tagihan/beban/SO/DP/..., pembatalan produksi) -- memperbaiki tiap jalur =
-- jalur ke-21 akan lupa lagi. Satu penjaga di DB: AFTER DELETE per tabel entitas.
--
-- Yang DILEPAS: baris document_attachments (tautan) saja. Baris `documents` + objek MinIO TETAP
-- (dokumen hub bisa tertaut ke entitas lain; sama dengan detach manual, #22). Berkas MILIK
-- (sales_invoice_attachments / bill_attachments) diurus kode rute (hapus objek sesudah commit).
--
-- Daftar tabel = routers/documents.py _ENTITAS_BACA (hub) -- tes unit
-- test_lampiran_yatim.py MEMERAH bila peta hub dan daftar trigger di sini berbeda.
--
-- CATATAN JUJUR (Law 34):
-- * Hanya hapus-KERAS. Entitas yang dihapus-LUNAK (deleted_at) tetap bertaut -- itu bukan yatim.
-- * Yatim LAMA (18 tautan expense, 3 sales_invoice_attachments kaos) TIDAK disentuh migrasi ini
--   (putusan MASTER: jangan hapus yatim lama tanpa keputusan).
-- * TRUNCATE tidak memicu trigger baris.
-- Aditif: fungsi + trigger baru; nol perubahan data. Rilis = migrasi-saja DULUAN.

CREATE OR REPLACE FUNCTION lepas_tautan_dokumen_entitas() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM document_attachments
     WHERE entity_type = TG_ARGV[0]
       AND entity_id = OLD.id
       AND tenant_id = OLD.tenant_id::text;
    RETURN OLD;
END;
$$;

-- Indeks pencarian sudah ada: idx_da_entity (entity_type, entity_id) -- tak ditambah.

DROP TRIGGER IF EXISTS trg_lepas_dokumen ON sales_invoices;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON sales_invoices
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('sales_invoice');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON bills;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON bills
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('bill');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON expenses;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON expenses
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('expense');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON customers;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON customers
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('customer');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON vendors;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON vendors
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('vendor');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON products;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON products
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('item');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON journal_entries;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('journal');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON quotes;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON quotes
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('quote');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON purchase_orders;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('purchase_order');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON sales_orders;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON sales_orders
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('sales_order');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON sales_receipts;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON sales_receipts
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('sales_receipt');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON receive_payments;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON receive_payments
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('payment');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON bill_payments_v2;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON bill_payments_v2
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('payment');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON credit_notes;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON credit_notes
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('credit_note');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON vendor_credits;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON vendor_credits
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('vendor_credit');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON stock_adjustments;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON stock_adjustments
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('stock_adjustment');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON stock_transfers;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON stock_transfers
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('stock_transfer');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON employees;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON employees
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('employee');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON fixed_assets;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON fixed_assets
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('asset');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON proformas;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON proformas
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('proforma');
DROP TRIGGER IF EXISTS trg_lepas_dokumen ON customer_deposits;
CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON customer_deposits
    FOR EACH ROW EXECUTE FUNCTION lepas_tautan_dokumen_entitas('customer_deposit');
