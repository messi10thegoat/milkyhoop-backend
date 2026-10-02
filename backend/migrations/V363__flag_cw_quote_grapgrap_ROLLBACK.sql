-- Rollback V363: cabut tiga flag Penawaran CW dari grapgrap (kembali ke halaman penawaran lama).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN
  ('conversational_workspace_quote', 'conversational_detail_quote', 'conversational_convert_page_quote');
