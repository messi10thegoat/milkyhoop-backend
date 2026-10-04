-- Rollback V383: cabut flag panel Dok. Penawaran CW dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_doc_panel_quote';
