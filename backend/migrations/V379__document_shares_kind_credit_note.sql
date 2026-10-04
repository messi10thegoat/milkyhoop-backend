-- V379 (U5 Nota Kredit, 4 Okt 2026, MASTER GO): jenis dokumen 'credit_note' untuk tautan publik P5 (document_shares).
-- CHECK kind di V355 hanya memuat 6 jenis -> INSERT kind='credit_note' akan ditolak. Aditif murni: melonggarkan satu CHECK,
-- nol perubahan baris/kolom. Idempoten: DROP IF EXISTS + ADD. Kode yang memakai kind baru (routers/dokumen.py) menyusul
-- SESUDAH migrasi ini (migrasi aditif DULU).
ALTER TABLE document_shares DROP CONSTRAINT IF EXISTS document_shares_kind_check;
ALTER TABLE document_shares ADD CONSTRAINT document_shares_kind_check
    CHECK (kind IN ('rekap', 'quotation', 'proforma', 'receipt', 'delivery', 'invoice', 'credit_note'));
