-- Rollback V409: cabut cf_submode + conversational_master_inline_so_si dari grapgrap (kaos tetap).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('cf_submode', 'conversational_master_inline_so_si');
