-- V291 (unit 3c): sales_invoice_items.dpp_harga_jual -- DPP harga jual per baris (neto sesudah
-- alokasi diskon dokumen), DISIMPAN saat faktur dibuat/disunting, di samping `dpp` (dasar yang
-- BENAR-BENAR dikenai tarif: == harga jual bila faktor 1/1, == DPP nilai lain bila 11/12).
--
-- Kenapa disimpan, bukan diturunkan saat cetak: faktor DPP hidup di tax_codes (V289) dan bisa
-- diubah (grapgrap PPN-12-OUT -> 11/12). Menurunkan harga jual dari faktor HARI INI akan menulis
-- ulang faktur lama diam-diam. PDF mencetak baris "DPP" + "DPP Nilai Lain" hanya bila baris
-- tersimpan menunjukkan dpp_harga_jual != dpp.
--
-- NULL untuk baris lama = "tidak tercatat" -> PDF tidak mencetak baris DPP (sama dengan hari ini).
-- Terukur 23 Sep 2026: 0 baris faktur non-void bertarif 12% di produksi -> tak ada yang perlu diisi.
--
-- Law 19: kolom nominal baru ikut dibekukan pada faktur yang sudah dibukukan (daftar V253 + 1).

ALTER TABLE sales_invoice_items
    ADD COLUMN IF NOT EXISTS dpp_harga_jual numeric(18,2);

DROP TRIGGER IF EXISTS trg_law19_baris_beku ON sales_invoice_items;
CREATE TRIGGER trg_law19_baris_beku BEFORE UPDATE ON sales_invoice_items
    FOR EACH ROW EXECUTE FUNCTION law19_bekukan_baris(
        'sales_invoices', 'invoice_id',
        'quantity', 'unit_price', 'discount_percent', 'discount_amount', 'tax_rate', 'tax_amount',
        'subtotal', 'total', 'dpp', 'dpp_harga_jual');
