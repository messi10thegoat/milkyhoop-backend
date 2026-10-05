-- ROLLBACK V392: cabut dua flag D-standar kaos (D4 Pengiriman, D5 Penerimaan).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi'
  AND feature IN ('conversational_detail_std_dl', 'conversational_detail_std_rp');
