-- Rollback V411: KODE dulu dikembalikan (kode baru menulis kolom ini), baru kolom dihapus. Nama per tautan hilang.
ALTER TABLE document_attachments DROP COLUMN IF EXISTS display_name;
