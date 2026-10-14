-- V422 (14 Okt 2026, BACKEND; W-20261014-DEPOSIT-JENIS, kontrak disetujui MASTER, pemilik setuju 14 Okt) -- jenis
-- penerimaan uang sebelum faktur: 'DP' (uang muka) | 'PELUNASAN'. HANYA LABEL: pembukuan tetap customer deposit
-- (pola NetSuite); plafon, posting, penerapan, refund, jurnal TIDAK membaca kolom ini.
-- NULL = baris lama -> jenis EFEKTIF diturunkan saat BACA (services/deposit_jenis): cd.jenis -> purpose proforma
-- (DP/TERMIN->DP, PELUNASAN->PELUNASAN) -> 'DP'. Tanpa backfill (putusan MASTER A).
-- Urutan: migrasi DULU, baru kode (kode baru membaca kolom ini). Kode lama + kolom baru = aman (kolom NULL tak dibaca).
ALTER TABLE customer_deposits ADD COLUMN IF NOT EXISTS jenis TEXT NULL;
ALTER TABLE customer_deposits DROP CONSTRAINT IF EXISTS chk_cust_deposit_jenis;
ALTER TABLE customer_deposits ADD CONSTRAINT chk_cust_deposit_jenis CHECK (jenis IS NULL OR jenis IN ('DP', 'PELUNASAN'));
