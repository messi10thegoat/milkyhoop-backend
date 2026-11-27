-- Pembalik V516. Menghapus daftar akses (pengguna kembali ke SEMUA gudang).
DROP TABLE IF EXISTS user_warehouse_access;
DELETE FROM schema_migrations WHERE version = 'V516__akses_gudang_pengguna.sql';
