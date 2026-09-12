-- ============================================================================
-- ROLLBACK V240 — pulihkan dwi-status beban ke nilai SEBELUM perapian
-- ============================================================================
-- Memulihkan dari snapshot, BUKAN dengan membalik pemetaan.
--
-- SEBABNYA PENTING: nilai sesudah-perbaikan ('VOID'/'REVERSED') identik dengan
-- nilai yang ditulis jalur tulis BARU untuk baris yang TAK PERNAH tersangkut.
-- Rollback "balik-petakan" tak bisa membedakan keduanya, jadi ia akan menyeret
-- baris yang benar kembali ke keadaan salah. Snapshot memulihkan TEPAT baris
-- yang disentuh V240, tak lebih.
--
-- ⚠️ Ini memulihkan nilai yang SALAH (itulah maksud rollback). Jalankan hanya
-- kalau perapian V240 sendiri yang bermasalah.
-- ============================================================================

UPDATE expenses e SET
    operational_status = s.operational_status_lama,
    accounting_status  = s.accounting_status_lama,
    updated_at = NOW()
FROM v240_snapshot_beban_dwi_status s
WHERE e.id = s.expense_id;

DROP TABLE IF EXISTS v240_snapshot_beban_dwi_status;
