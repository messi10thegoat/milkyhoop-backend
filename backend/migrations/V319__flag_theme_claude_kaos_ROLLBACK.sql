-- Rollback V319: cabut flag theme_claude dari kaos (FE kembali ke tema lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'theme_claude';
