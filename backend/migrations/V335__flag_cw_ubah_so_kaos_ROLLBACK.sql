-- Rollback V335: cabut flag conversational_edit_page_so dari kaos (FE kembali ke tema lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_edit_page_so';
