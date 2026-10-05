-- Rollback V388: cabut 6 flag Pengiriman/NK CW dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_workspace_dl', 'conversational_detail_dl', 'conversational_doc_panel_dl', 'conversational_form_dl', 'conversational_workspace_cn', 'conversational_detail_cn');
