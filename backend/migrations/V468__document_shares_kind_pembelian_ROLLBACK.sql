-- Rollback V468: kembalikan CHECK ke 7 jenis Penjualan (== V379). Tautan jenis Pembelian yang sudah dibuat HARUS dibuang dulu
-- (kalau tidak, ADD CONSTRAINT gagal): tautan itu berhenti berlaku. Kode JENIS_BAGIKAN Pembelian harus sudah dicabut lebih dulu.
DELETE FROM document_shares WHERE kind IN ('bill', 'debit_note', 'goods_receipt', 'payment_out', 'purchase_order', 'vendor_deposit');
ALTER TABLE document_shares DROP CONSTRAINT IF EXISTS document_shares_kind_check;
ALTER TABLE document_shares ADD CONSTRAINT document_shares_kind_check
    CHECK (kind IN ('rekap', 'quotation', 'proforma', 'receipt', 'delivery', 'invoice', 'credit_note'));
