-- Rollback V368: cabut flag kirim dokumen penawaran CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_doc_send_quote';
