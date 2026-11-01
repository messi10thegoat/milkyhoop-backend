-- V469 ROLLBACK: hapus tabel draf percakapan Milkybook v2. Draf bukan data pembukuan, tetapi pembalik GAGAL KERAS bila
-- masih ada draf yang belum kedaluwarsa (7 hari), supaya draf pengguna tidak hilang diam-diam.
DO $$
BEGIN
    IF to_regclass('milkybook_v2_percakapan') IS NOT NULL
       AND EXISTS (SELECT 1 FROM milkybook_v2_percakapan WHERE diubah >= NOW() - INTERVAL '7 days') THEN
        RAISE EXCEPTION 'V469 rollback ditolak: masih ada draf percakapan Milkybook v2 yang hidup';
    END IF;
END $$;
DROP TABLE IF EXISTS milkybook_v2_percakapan;
