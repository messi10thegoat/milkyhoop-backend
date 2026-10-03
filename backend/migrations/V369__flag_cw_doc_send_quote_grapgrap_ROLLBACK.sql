-- Rollback V369: cabut flag kirim dokumen penawaran CW dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_doc_send_quote';
