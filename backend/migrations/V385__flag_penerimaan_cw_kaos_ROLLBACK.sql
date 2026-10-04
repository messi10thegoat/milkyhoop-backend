-- Rollback V385: cabut 5 flag Penerimaan CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_workspace_rp', 'conversational_detail_rp', 'conversational_doc_panel_rp', 'conversational_cancel_page_rp', 'conversational_form_rp');
