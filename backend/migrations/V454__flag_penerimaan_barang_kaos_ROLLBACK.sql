-- ROLLBACK V454: matikan flag di kaos (nol jurnal; data tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('penerimaan_barang_v1');
