-- Rollback V336: cabut flag conversational_detail_si dari kaos (FE kembali ke tema lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_detail_si';
