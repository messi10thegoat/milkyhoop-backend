-- V468 (L7 bagikan massal Pembelian, 31 Okt 2026, MASTER GO; nomor dipesan MASTER): jenis dokumen Pembelian untuk tautan publik
-- (document_shares). CHECK V379 hanya memuat 7 jenis Penjualan -> INSERT kind Pembelian ditolak. CHECK baru = SEMUA jenis cetak
-- (routers/dokumen.JENIS_CETAK: 7 Penjualan + 6 Pembelian) supaya membuka jenis berikutnya tak perlu migrasi lagi; KODE membuka per
-- jenis (dokumen.JENIS_BAGIKAN; unit ini: purchase_order + bill). Aditif murni: melonggarkan satu CHECK, nol perubahan baris/kolom.
-- Idempoten: DROP IF EXISTS + ADD. Kode yang memakai kind baru menyusul SESUDAH migrasi ini (migrasi aditif DULU).
ALTER TABLE document_shares DROP CONSTRAINT IF EXISTS document_shares_kind_check;
ALTER TABLE document_shares ADD CONSTRAINT document_shares_kind_check
    CHECK (kind IN ('rekap', 'quotation', 'proforma', 'receipt', 'delivery', 'invoice', 'credit_note',
                    'bill', 'debit_note', 'goods_receipt', 'payment_out', 'purchase_order', 'vendor_deposit'));
