-- V497: K8 (14 Nov 2026) -- gudang SISTEM (lokasi Transit untuk Pemindahan Barang dua langkah ala Accurate). Penanda warehouses.is_system:
-- true = dikelola otomatis oleh sistem (dibuat KODE saat Kirim pertama, code 'SYS-TRANSIT'); TIDAK tampil di daftar/pilihan gudang, tak dipilih sebagai
-- gudang bawaan, tak bisa diubah/dihapus/dijadikan utama pengguna. Kartu stok & stok per gudang menampilkannya apa adanya.
-- DIIZINKAN LANGSUNG pemilik (14 Nov ~00:40, via MASTER): migrasi ADITIF, NOT NULL DEFAULT false, TANPA backfill (semua gudang lama = false). Pembalik: V497__warehouses_is_system_ROLLBACK.sql.
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN warehouses.is_system IS 'Gudang sistem (mis. Transit pemindahan dua langkah): dikelola kode, disaring dari daftar/pilihan/bawaan.';
