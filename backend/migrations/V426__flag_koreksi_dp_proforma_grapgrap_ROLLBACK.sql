-- ROLLBACK V426: matikan koreksi_dp_proforma di grapgrap (koreksi/pindah DP yang sudah terjadi TIDAK dibatalkan; nol jurnal).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'koreksi_dp_proforma';
