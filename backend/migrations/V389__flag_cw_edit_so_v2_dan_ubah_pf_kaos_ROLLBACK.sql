-- Rollback V389: cabut 2 flag dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_edit_so_v2', 'conversational_edit_page_pf');
