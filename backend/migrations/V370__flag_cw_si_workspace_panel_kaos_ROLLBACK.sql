-- Rollback V370: cabut flag daftar + panel faktur CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_workspace_si', 'conversational_doc_panel_si');
