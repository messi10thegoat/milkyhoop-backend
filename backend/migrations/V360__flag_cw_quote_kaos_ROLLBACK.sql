-- Rollback V360: cabut dua flag Penawaran CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi'
  AND feature IN ('conversational_workspace_quote', 'conversational_detail_quote');
