-- ROLLBACK V427: kembalikan kode ke versi pra-JUMLAH-BARIS DULU (kode baru menulis/membaca kolom ini), baru jalankan ini.
-- PERINGATAN: baris yang memakai jumlah baris KEHILANGAN penggantinya (kembali qty x harga) -> total dokumen bisa bergeser sen.
ALTER TABLE credit_note_items DROP COLUMN IF EXISTS jumlah_baris;
ALTER TABLE quote_items DROP COLUMN IF EXISTS jumlah_baris;
ALTER TABLE sales_invoice_items DROP COLUMN IF EXISTS jumlah_baris;
ALTER TABLE sales_order_items DROP COLUMN IF EXISTS jumlah_baris;
