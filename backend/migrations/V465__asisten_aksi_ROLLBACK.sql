-- ROLLBACK V465: hapus tabel audit Milkybook. Data audit IKUT terhapus -> jalankan hanya bila Milkybook belum dipakai,
-- atau sesudah pg_dump tabel ini (pg_dump -t asisten_aksi).
DROP TRIGGER IF EXISTS asisten_aksi_append_only ON asisten_aksi;
DROP FUNCTION IF EXISTS asisten_aksi_tolak_ubah();
DROP TABLE IF EXISTS asisten_aksi;
