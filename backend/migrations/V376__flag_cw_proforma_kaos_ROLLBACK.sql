-- Rollback V376: cabut 5 flag Proforma CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_workspace_pf', 'conversational_detail_pf', 'conversational_doc_panel_pf', 'conversational_cancel_page_pf', 'conversational_form_pf');
