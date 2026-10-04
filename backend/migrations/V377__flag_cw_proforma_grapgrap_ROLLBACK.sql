-- Rollback V377: cabut 5 flag Proforma CW dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_workspace_pf', 'conversational_detail_pf', 'conversational_doc_panel_pf', 'conversational_cancel_page_pf', 'conversational_form_pf');
