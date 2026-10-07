-- Rollback V401: cabut flag fase lokasi dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_fase_lokasi_so';
