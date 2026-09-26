-- ROLLBACK V317: grapgrap kembali ke keadaan V316 (4 flag CW mati; baris TIDAK dihapus). Butuh putusan pemilik.
UPDATE tenant_features SET enabled = false, updated_at = now()
WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_workspace_so', 'conversational_form_so',
                  'conversational_form_so_save', 'conversational_detail_so')
  AND enabled;
