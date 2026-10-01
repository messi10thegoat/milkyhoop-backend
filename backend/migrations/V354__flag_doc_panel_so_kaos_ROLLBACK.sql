-- Rollback V354: cabut flag conversational_doc_panel_so dari kaos (daftar Pesanan kembali ke kolom lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_doc_panel_so';
