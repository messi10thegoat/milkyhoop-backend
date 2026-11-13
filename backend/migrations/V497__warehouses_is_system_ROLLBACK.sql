-- ROLLBACK V497: buang penanda gudang sistem. Kode K8 membaca kolom ini (daftar/bawaan/pilihan menyaring NOT is_system), jadi kembalikan KODE ke sebelum K8
-- SEBELUM menjalankan berkas ini. Gudang Transit yang sudah dibuat (code 'SYS-TRANSIT') menjadi gudang biasa bila kolom dibuang: hapus/nonaktifkan manual bila tak ada stoknya.
ALTER TABLE warehouses DROP COLUMN IF EXISTS is_system;
