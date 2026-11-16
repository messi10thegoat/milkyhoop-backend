-- ROLLBACK V502: buang kolom merek barang. Kode BUG-260 (items.py create/update/list/detail/duplikat/ekspor, impor_barang) membaca kolom ini, jadi kembalikan KODE
-- ke sebelum V502 SEBELUM menjalankan berkas ini. Nilai merek yang sudah diisi HILANG (tak ada salinan); ekspor Barang dulu bila perlu. Indeks ikut terbuang bersama kolom.
DROP INDEX IF EXISTS idx_products_tenant_merek;
ALTER TABLE products DROP COLUMN IF EXISTS merek;
