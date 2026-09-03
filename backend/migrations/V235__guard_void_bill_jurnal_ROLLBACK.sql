-- ROLLBACK V235: cabut penjaga void-bill.
--
-- Sesudah ini, `UPDATE bills SET status_v2='void'` kembali bisa dilakukan
-- meski jurnal BILL-nya masih POSTED tanpa pembalik — yaitu keadaan yang
-- melahirkan BILL-2609-0002. Itu memang arti rollback di sini.
--
-- Jalur aplikasi (`void_bill`) TIDAK terpengaruh baik dipasang maupun dicabut:
-- ia selalu membalik jurnal lebih dulu, jadi penjaga ini tak pernah menyentuhnya.
-- Nol dampak pada data yang sudah ada — trigger ini hanya memeriksa UPDATE baru.

DROP TRIGGER IF EXISTS trg_bills_guard_void_jurnal ON bills;
DROP FUNCTION IF EXISTS trg_guard_void_bill_jurnal();
