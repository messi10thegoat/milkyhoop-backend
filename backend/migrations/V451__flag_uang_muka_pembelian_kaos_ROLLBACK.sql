-- ROLLBACK V451: matikan flag UI UM Pembelian seragam di kaos (nol jurnal; data UM tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi'
  AND feature IN ('listpage_uang_muka_pembelian_v1', 'detailpage_uang_muka_pembelian_v1');
