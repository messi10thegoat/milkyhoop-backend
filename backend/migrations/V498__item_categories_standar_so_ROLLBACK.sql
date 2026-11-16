-- ROLLBACK V498: buang kolom master Kategori standar SO. Kode K-2..K-4 (kategori_aksi, kategori_daftar, items.py kategori) membaca kolom ini -> kembalikan KODE ke
-- sebelum K-2 SEBELUM menjalankan berkas ini. Kategori yang sudah dinonaktifkan KEMBALI aktif (info hilang), bawaan/induk/deskripsi hilang; barang tak berubah.
DROP INDEX IF EXISTS idx_item_categories_parent;
ALTER TABLE item_categories DROP CONSTRAINT IF EXISTS chk_item_categories_bukan_induk_sendiri;
ALTER TABLE item_categories DROP CONSTRAINT IF EXISTS chk_item_categories_default_aktif;
DROP INDEX IF EXISTS uq_item_categories_default;
ALTER TABLE item_categories DROP COLUMN IF EXISTS updated_at;
ALTER TABLE item_categories DROP COLUMN IF EXISTS description;
ALTER TABLE item_categories DROP COLUMN IF EXISTS parent_id;
ALTER TABLE item_categories DROP COLUMN IF EXISTS is_default;
ALTER TABLE item_categories DROP COLUMN IF EXISTS is_active;
