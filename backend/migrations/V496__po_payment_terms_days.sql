-- V496: BUG-205 (13 Nov 2026) -- syarat bayar DISIMPAN di Pesanan Pembelian (snapshot per dokumen, pola Accurate). Kolom purchase_orders.payment_terms_days:
-- jumlah hari termin (0 = Tunai), NULL = tak ada syarat bayar tercatat. Bawaan saat PO dibuat = termin vendor saat itu (diisi KODE, bukan migrasi); bisa diubah di form PO
-- selama PO Draf/Terkirim; sesudahnya terkunci. Tagihan dari PO memakai termin PO bila ada (urutan: isian pengguna > termin PO > termin vendor > tanggal faktur).
-- DISETUJUI LANGSUNG pemilik (13 Nov ~15:20, via MASTER): migrasi ADITIF, nullable. TANPA BACKFILL: PO lama = NULL (tak dicetak, jatuh tempo tagihan tak berubah);
-- JANGAN mengisi dari vendors tanpa tanya pemilik. Pembalik: V496__po_payment_terms_days_ROLLBACK.sql.
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS payment_terms_days INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_po_payment_terms_days') THEN
        ALTER TABLE purchase_orders ADD CONSTRAINT chk_po_payment_terms_days
            CHECK (payment_terms_days IS NULL OR (payment_terms_days >= 0 AND payment_terms_days <= 365));
    END IF;
END $$;
COMMENT ON COLUMN purchase_orders.payment_terms_days IS 'Syarat bayar PO (hari; 0 = Tunai; NULL = tak tercatat). Snapshot saat PO dibuat (bawaan termin vendor), tak mengikuti vendor sesudahnya.';
