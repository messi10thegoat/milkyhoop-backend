-- Rollback V407: buang tabel serah-terima. GAGAL BERISIK bila sudah berisi data (jangan membuang catatan resi/serah
-- diam-diam); kosongkan secara sadar dulu bila memang dimaksudkan. Jalankan dengan psql -v ON_ERROR_STOP=1 -1.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pengiriman_serah) OR EXISTS (SELECT 1 FROM pengiriman_resi) THEN
        RAISE EXCEPTION 'Rollback V407: pengiriman_serah/pengiriman_resi sudah berisi data -- tidak dibuang otomatis';
    END IF;
END $$;
DROP TABLE pengiriman_preferensi;
DROP TABLE pengiriman_resi;
DROP TABLE pengiriman_serah;
DROP TABLE metode_serah;
