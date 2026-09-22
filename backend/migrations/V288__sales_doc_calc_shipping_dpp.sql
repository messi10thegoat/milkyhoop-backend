-- V288: kalkulator PPN/diskon bersama untuk Faktur, SO, dan SO->Faktur
-- (services/sales_doc_calc.py). Tiga perubahan ADITIF, nol data diubah:
--
-- 1. sales_invoices.shipping_amount -- SO->Faktur kini MEMBAWA ongkir SO. Sebelum ini
--    faktur tidak punya tempat untuknya dan ongkir SO tak pernah tertagih. Ongkir masuk
--    total DI LUAR DPP (perilaku SO yang dipertahankan); apakah ongkir kena PPN adalah
--    pertanyaan kebijakan untuk konsultan pajak pemilik, sengaja TIDAK diputuskan di sini.
--    DEFAULT 0 NOT NULL: semua faktur lama tetap bermakna sama (ongkir 0).
--
-- 2. sales_order_items.dpp -- DPP per baris yang BENAR-BENAR dipakai menghitung PPN
--    (sesudah alokasi diskon dokumen), sama dengan sales_invoice_items.dpp. NULL untuk
--    baris lama (dihitung dengan aturan lama); terisi pada simpan berikutnya bila draft.
--
-- 3. Law 19: shipping_amount ikut dibekukan pada faktur yang sudah dibukukan. Kolom
--    nominal baru tidak boleh menjadi satu-satunya kolom uang yang masih bisa diubah
--    (total_amount sudah beku, jadi uang aman -- ini soal konsistensi pagar).

ALTER TABLE sales_invoices
    ADD COLUMN IF NOT EXISTS shipping_amount numeric(18,2) NOT NULL DEFAULT 0;

ALTER TABLE sales_order_items
    ADD COLUMN IF NOT EXISTS dpp numeric(18,2);

DROP TRIGGER IF EXISTS trg_law19_bekukan_nominal ON sales_invoices;
CREATE TRIGGER trg_law19_bekukan_nominal
    BEFORE UPDATE ON public.sales_invoices
    FOR EACH ROW WHEN ((old.journal_id IS NOT NULL))
    EXECUTE FUNCTION law19_bekukan_nominal(
        'subtotal', 'discount_percent', 'discount_amount',
        'tax_rate', 'tax_amount', 'total_amount', 'shipping_amount'
    );
