-- Rollback V378: cabut 7 flag Uang Muka CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_workspace_dp', 'conversational_detail_dp', 'conversational_doc_panel_dp', 'conversational_form_dp', 'conversational_void_page_dp', 'conversational_refund_page_dp', 'conversational_apply_page_dp');
