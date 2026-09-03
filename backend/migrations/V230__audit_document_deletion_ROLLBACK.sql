-- ROLLBACK V230: cabut 7 trigger audit penghapusan + fungsinya.
--
-- PERINGATAN: baris `audit_logs` ber-`eventType='DOCUMENT_DELETED'` yang sudah
-- tercatat TIDAK dihapus di sini, dan memang TIDAK BISA — `audit_logs`
-- ditegakkan append-only oleh `trg_audit_immutable` dan
-- `trg_prevent_audit_log_delete`, yang me-RAISE EXCEPTION pada setiap DELETE.
-- Itu perilaku yang BENAR: jejak audit tidak boleh bisa dihapus oleh rollback
-- migrasi. Rollback ini hanya menghentikan pencatatan BARU.
--
-- Sesudah rollback, penghapusan dokumen kembali TIDAK meninggalkan jejak apa
-- pun — keadaan sebelum V230, yang membuat forensik 2026-09-03 buntu.
--
-- Nol dampak akuntansi: fungsi dan trigger ini tak pernah menyentuh jurnal.

DROP TRIGGER IF EXISTS trg_log_deletion ON sales_invoices;
DROP TRIGGER IF EXISTS trg_log_deletion ON sales_orders;
DROP TRIGGER IF EXISTS trg_log_deletion ON quotes;
DROP TRIGGER IF EXISTS trg_log_deletion ON bills;
DROP TRIGGER IF EXISTS trg_log_deletion ON proformas;
DROP TRIGGER IF EXISTS trg_log_deletion ON customer_deposits;
DROP TRIGGER IF EXISTS trg_log_deletion ON purchase_orders;

DROP FUNCTION IF EXISTS log_document_deletion();

DELETE FROM schema_migrations WHERE version = 'V230__audit_document_deletion.sql';
