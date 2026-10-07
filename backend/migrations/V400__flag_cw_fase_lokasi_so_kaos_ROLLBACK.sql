-- Rollback V400: cabut flag fase lokasi dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_fase_lokasi_so';
