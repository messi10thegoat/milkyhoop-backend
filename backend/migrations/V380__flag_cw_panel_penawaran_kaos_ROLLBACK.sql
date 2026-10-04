-- Rollback V380: cabut flag panel Dok. Penawaran CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_doc_panel_quote';
