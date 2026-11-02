-- V473: sisa tagihan pada alokasi pembayaran tagihan menyimpan SEN (U-AP-SEN, 2 Nov 2026, BACKEND2; nomor dipesan MASTER;
-- putusan pemilik LANGSUNG 2 Nov "samakan Penjualan": bayar tagihan menerima sen, Decimal 2 desimal, pola NetSuite/SAP).
-- bill_payment_allocations.remaining_before / remaining_after BIGINT (V088) -> numeric(18,2), setara amount_applied (V115).
-- bigint -> numeric TANPA kehilangan nilai (semua bigint masuk numeric(18,2) selama |n| < 10^16). Tanpa view/aturan/fungsi/trigger
-- yang bergantung pada kedua kolom (diukur pg_depend/pg_proc 2 Nov); 29 baris saat diukur -> tulis-ulang tabel singkat.
-- Pembalik: V473__bill_payment_alloc_sisa_sen_ROLLBACK.sql (menolak bila sudah ada nilai bersen).
ALTER TABLE bill_payment_allocations
    ALTER COLUMN remaining_before TYPE numeric(18,2) USING remaining_before::numeric(18,2),
    ALTER COLUMN remaining_after TYPE numeric(18,2) USING remaining_after::numeric(18,2);
