-- ROLLBACK V229: buang tabel user_profiles.
--
-- PERINGATAN: ini MENGHAPUS DATA — setiap nama tampilan yang sudah disetel
-- pengguna hilang dan tak bisa dipulihkan dari tabel lain (`"User".name`
-- adalah kolom terpisah yang diisi saat pendaftaran, bukan salinannya).
-- Ambil dump dulu bila barisnya sudah terisi.
--
-- Sesudah rollback, `GET /api/user/profile` kembali 500 pada setiap muat
-- halaman ber-auth — itu keadaan sebelum V229, bukan kerusakan baru.
--
-- Nol dampak akuntansi: tabel ini tak pernah menyentuh jurnal.

DROP TABLE IF EXISTS user_profiles;

DELETE FROM schema_migrations WHERE version = 'V229__user_profiles.sql';
