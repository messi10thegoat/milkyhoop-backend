-- Rollback V338: cabut flag conversational_detail_si dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_detail_si';
