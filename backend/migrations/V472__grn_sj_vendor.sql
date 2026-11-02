-- V472: No. SJ VENDOR pada Penerimaan Barang (GRN-SJ-VENDOR, 2 Nov 2026, BACKEND2; nomor dipesan MASTER; pola SAP MIGO
-- "Delivery Note" / NetSuite Item Receipt reference). Diisi lewat POST /purchase-orders/{id}/receive (vendor_delivery_note),
-- tampil di daftar/detail GRN, PDF, ekspor (Referensi). TANPA unique: vendor boleh memakai ulang nomor -- pratinjau memberi
-- catatan SJ_VENDOR_SUDAH_DIPAKAI (bukan blok). String kosong ditolak (app menormalkan "" -> NULL).
-- ADITIF: kolom baru NULL, nol data lama disentuh, tanpa backfill. Indeks biasa (apply_mig menolak CONCURRENTLY; tabel kecil).
-- Pembalik: V472__grn_sj_vendor_ROLLBACK.sql.
ALTER TABLE goods_receipts ADD COLUMN IF NOT EXISTS vendor_delivery_note TEXT NULL;
ALTER TABLE goods_receipts DROP CONSTRAINT IF EXISTS goods_receipts_sj_vendor_cek;
ALTER TABLE goods_receipts ADD CONSTRAINT goods_receipts_sj_vendor_cek
    CHECK (vendor_delivery_note IS NULL OR char_length(vendor_delivery_note) BETWEEN 1 AND 100);
CREATE INDEX IF NOT EXISTS idx_goods_receipts_sj_vendor ON goods_receipts (tenant_id, vendor_id, vendor_delivery_note)
    WHERE vendor_delivery_note IS NOT NULL AND status <> 'void';
