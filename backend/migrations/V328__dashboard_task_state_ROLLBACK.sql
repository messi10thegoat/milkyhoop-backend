-- Rollback V328: hapus status "Selesai" dashboard (hanya preferensi tampilan; nol dampak buku).
DROP TABLE IF EXISTS dashboard_task_state;
