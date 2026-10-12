-- Rollback V414: cabut koreksi_dp_proforma dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'koreksi_dp_proforma';
