-- Rollback V356: cabut flag conversational_doc_panel_so dari grapgrap (daftar Pesanan kembali ke kolom lama + klik =
-- detail; tak ada data yang perlu dipulihkan).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_doc_panel_so';
