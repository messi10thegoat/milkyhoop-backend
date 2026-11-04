-- V478 ROLLBACK (BACKEND2 5 Nov 2026): hanya bila kode B0 sudah dibalik DULU (penulis faktur membaca is_opening_balance).
-- Faktur pembuka yang sudah terbit TETAP faktur biasa (jurnal INVOICE Dr Piutang / Cr Modal Saldo Awal); hanya penanda + indeks yang dibuang.
DROP INDEX IF EXISTS uq_si_saldo_awal_pelanggan;
ALTER TABLE sales_invoices DROP COLUMN IF EXISTS is_opening_balance;
