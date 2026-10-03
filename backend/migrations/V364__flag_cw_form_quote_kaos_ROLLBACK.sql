-- Rollback V364: cabut flag form Penawaran CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_form_quote';
