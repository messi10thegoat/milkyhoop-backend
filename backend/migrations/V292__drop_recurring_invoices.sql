-- V292 (23 Sep 2026): hapus tabel Faktur Berulang -- keputusan pemilik "Dihapus saja".
-- Kode fiturnya sudah dicabut di d79500cc. Fitur ini MATI sejak lahir: generate() memanggil
-- generate_invoice_number() (tak ada) dan menulis 4 kolom yang tak ada di sales_invoice_items.
--
-- PENJAGA DI DALAM SQL (bukan di kepala): dihitung ulang SAAT DITERAPKAN, dan migrasi
-- DIBATALKAN bila ada satu baris pun -- recurring_invoices, recurring_invoice_items,
-- sales_invoices.recurring_invoice_id terisi, sales_invoices.is_recurring = true.
-- Terukur 23 Sep (semua tenant, read-only): keempatnya 0.
--
-- KOLOM MATI YANG SENGAJA DIBIARKAN: sales_invoices.recurring_invoice_id dan
-- sales_invoices.is_recurring. Tak dipakai kode mana pun sesudah d79500cc, 0 baris terisi.
-- TIDAK di-drop: sales_invoices adalah tabel paling sentral dan berada di bawah pemicu
-- pembeku Law 19; membuang kolom tak memberi apa-apa dan mempertaruhkan pemicunya.
-- JANGAN mengira keduanya hidup. FK-nya (satu-satunya rujukan ke tabel ini) dicabut di sini.
--
-- Idempoten: aman diterapkan dua kali (IF EXISTS + penjaga memakai to_regclass).
-- Recurring BILLS (recurring_bills, recurring_bill_items, get_due_recurring_bills) TIDAK disentuh.

DO $$
DECLARE n bigint;
BEGIN
    IF to_regclass('public.recurring_invoices') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM recurring_invoices' INTO n;
        IF n <> 0 THEN RAISE EXCEPTION 'V292 dibatalkan: recurring_invoices berisi % baris', n; END IF;
    END IF;
    IF to_regclass('public.recurring_invoice_items') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM recurring_invoice_items' INTO n;
        IF n <> 0 THEN RAISE EXCEPTION 'V292 dibatalkan: recurring_invoice_items berisi % baris', n; END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'sales_invoices' AND column_name = 'recurring_invoice_id') THEN
        EXECUTE 'SELECT count(*) FROM sales_invoices WHERE recurring_invoice_id IS NOT NULL' INTO n;
        IF n <> 0 THEN RAISE EXCEPTION 'V292 dibatalkan: % faktur merujuk recurring_invoice_id', n; END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'sales_invoices' AND column_name = 'is_recurring') THEN
        EXECUTE 'SELECT count(*) FROM sales_invoices WHERE is_recurring' INTO n;
        IF n <> 0 THEN RAISE EXCEPTION 'V292 dibatalkan: % faktur ber-is_recurring', n; END IF;
    END IF;
END $$;

ALTER TABLE sales_invoices DROP CONSTRAINT IF EXISTS sales_invoices_recurring_invoice_id_fkey;
DROP TABLE IF EXISTS recurring_invoice_items;   -- pemicu trg_update_ri_totals ikut hilang
DROP TABLE IF EXISTS recurring_invoices;        -- trg_check_ri_completion, trg_recurring_invoices_updated_at ikut hilang
DROP FUNCTION IF EXISTS check_recurring_invoice_completion();
DROP FUNCTION IF EXISTS update_recurring_invoice_totals();
DROP FUNCTION IF EXISTS update_recurring_invoices_updated_at();
DROP FUNCTION IF EXISTS get_due_recurring_invoices(text, date);
