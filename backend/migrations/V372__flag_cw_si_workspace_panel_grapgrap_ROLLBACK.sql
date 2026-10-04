-- Rollback V372: cabut daftar + panel Faktur CW dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_workspace_si', 'conversational_doc_panel_si');
