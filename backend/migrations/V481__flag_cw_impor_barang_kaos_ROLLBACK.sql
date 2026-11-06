-- ROLLBACK V481: matikan flag CW Impor barang di kaos (nol jurnal; barang yang sudah diimpor tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_impor_barang';
