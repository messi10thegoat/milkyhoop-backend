-- ROLLBACK V312. Hanya aman bila tabel KOSONG (baris = porsi NK yang sudah menurunkan allocated_amount;
-- menghapusnya membuat void NK tak bisa mencerminkan). Gagal-keras bila ada isinya.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM credit_note_deferral_lines) THEN
        RAISE EXCEPTION 'ROLLBACK V312 ditolak: credit_note_deferral_lines berisi data (void NK terkait dulu)';
    END IF;
END $$;
DROP FUNCTION IF EXISTS verify_cn_deferral_all();
DROP TABLE IF EXISTS credit_note_deferral_lines;
