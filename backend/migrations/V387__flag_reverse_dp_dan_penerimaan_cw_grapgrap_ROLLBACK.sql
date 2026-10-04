-- Rollback V387: cabut 6 flag (reverse_page_dp + 5 _rp) dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_reverse_page_dp', 'conversational_workspace_rp', 'conversational_detail_rp', 'conversational_doc_panel_rp', 'conversational_cancel_page_rp', 'conversational_form_rp');
