-- Rollback V396: cabut flag bulk_write dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_bulk_write';
