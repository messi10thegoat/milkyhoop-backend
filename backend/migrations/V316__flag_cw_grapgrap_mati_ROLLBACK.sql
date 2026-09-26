-- ROLLBACK V316: nyalakan lagi dua flag CW grapgrap (butuh putusan pemilik). Idempoten.
UPDATE tenant_features SET enabled = true, updated_at = now()
WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_workspace_so', 'conversational_form_so')
  AND NOT enabled;
