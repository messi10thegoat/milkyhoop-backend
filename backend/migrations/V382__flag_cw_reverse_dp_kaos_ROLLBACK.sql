-- Rollback V382: cabut flag reverse_page_dp dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_reverse_page_dp';
