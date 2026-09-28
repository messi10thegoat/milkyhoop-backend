-- Rollback V324: cabut flag dashboard_v2 dari kaos (FE kembali ke tema lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'dashboard_v2';
