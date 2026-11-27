-- V514: SERIAL S3 (GO pemilik LANGSUNG 27 Nov ±10:50 via MASTER: retur jual/beli + Pemindahan, SATU migrasi, flag serial_v1 gelap).
-- ADITIF, 0 data: nomor seri baris DRAF (opsi a, pola V513 stock_adjustment_items.serials), dinilai pintu kartu stok saat posting/selesai.
-- Pembalik: V514__serial_retur_pemindahan_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig).
ALTER TABLE credit_note_items ADD COLUMN serials TEXT[];
ALTER TABLE vendor_credit_items ADD COLUMN serials TEXT[];
ALTER TABLE stock_transfer_items ADD COLUMN serials TEXT[];
