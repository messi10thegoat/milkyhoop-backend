-- Rollback V379: kembalikan CHECK ke 6 jenis. Tautan 'credit_note' yang sudah dibuat HARUS dibuang dulu
-- (kalau tidak, ADD CONSTRAINT gagal): tautan itu berhenti berlaku.
DELETE FROM document_shares WHERE kind = 'credit_note';
ALTER TABLE document_shares DROP CONSTRAINT IF EXISTS document_shares_kind_check;
ALTER TABLE document_shares ADD CONSTRAINT document_shares_kind_check
    CHECK (kind IN ('rekap', 'quotation', 'proforma', 'receipt', 'delivery', 'invoice'));
