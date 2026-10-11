-- Rollback V406: cabut cf_submode dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'cf_submode';
