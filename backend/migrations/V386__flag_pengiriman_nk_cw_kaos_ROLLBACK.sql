-- Rollback V386: cabut 6 flag Pengiriman/NK CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_workspace_dl', 'conversational_detail_dl', 'conversational_doc_panel_dl', 'conversational_form_dl', 'conversational_workspace_cn', 'conversational_detail_cn');
