-- Rollback V420: KODE dulu dikembalikan (kode baru membaca/menulis kolom ini), baru kolom dihapus. Identitas pajak yang
-- sudah diisi pengguna HILANG -- ekspor dulu bila ada isinya.
ALTER TABLE customers DROP COLUMN IF EXISTS nitku, DROP COLUMN IF EXISTS jenis_id,
    DROP COLUMN IF EXISTS negara, DROP COLUMN IF EXISTS nomor_dokumen;
ALTER TABLE vendors DROP COLUMN IF EXISTS nitku, DROP COLUMN IF EXISTS negara;
