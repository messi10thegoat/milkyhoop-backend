-- Rollback V395: cabut flag bulk_list dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_bulk_list';
