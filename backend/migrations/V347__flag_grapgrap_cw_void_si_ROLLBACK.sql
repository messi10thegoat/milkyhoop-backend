-- Rollback V347: cabut flag conversational_void_page_si dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_void_page_si';
