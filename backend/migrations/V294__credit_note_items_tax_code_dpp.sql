-- V294 (PPN 3c): credit_note_items gets the same per-line tax identity as sales_invoice_items.
--   tax_code_id    -- the line's PPN code (was only a free-text `tax_code`); lets the DTL write
--                     pick the code per line instead of "any active PPN code, LIMIT 1, no ORDER BY"
--                     (which could be an INPUT code on an OUTPUT credit note).
--   dpp            -- base actually taxed (DPP nilai lain when the code's factor is 11/12).
--   dpp_harga_jual -- line net after line discount and doc-discount share (future e-Faktur retur).
-- Additive, nullable: old rows = NULL = not recorded. Measured 23 Sep: 7 credit notes live, none
-- taxed or discounted, 0 CREDIT_NOTE document_tax_lines -> nothing to backfill. Idempotent.
ALTER TABLE credit_note_items ADD COLUMN IF NOT EXISTS tax_code_id uuid REFERENCES tax_codes(id);
ALTER TABLE credit_note_items ADD COLUMN IF NOT EXISTS dpp numeric(18,2);
ALTER TABLE credit_note_items ADD COLUMN IF NOT EXISTS dpp_harga_jual numeric(18,2);
