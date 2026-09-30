-- V348 (MASTER 30 Sep 2026, NK "dari faktur", pola NetSuite/SAP: baris credit memo merujuk baris faktur).
-- credit_note_items.original_invoice_item_id: tautan baris NK -> baris faktur asal. Dulu medan request
-- CreateCreditNoteRequest.items[].original_invoice_item_id diterima lalu DIBUANG DIAM di INSERT, sehingga "sudah
-- dinotakan per baris" tak bisa dihitung. Aditif, NULL, TANPA isi ulang (diukur 30 Sep: 1 NK bertaut faktur di
-- grapgrap, alasan diskon, tanpa baris; NK lama = "tak bertaut", dicatat eksplisit di pratinjau, tidak ditebak).
-- Validasi tenant/faktur di kode (credit_notes.periksa_tautan_baris_faktur); FK menjaga keberadaan baris.

ALTER TABLE credit_note_items
    ADD COLUMN IF NOT EXISTS original_invoice_item_id uuid NULL
        REFERENCES sales_invoice_items(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_cni_original_invoice_item
    ON credit_note_items (original_invoice_item_id) WHERE original_invoice_item_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'credit_note_items' AND column_name = 'original_invoice_item_id') THEN
        RAISE EXCEPTION 'V348: kolom credit_note_items.original_invoice_item_id tidak mendarat';
    END IF;
END $$;
