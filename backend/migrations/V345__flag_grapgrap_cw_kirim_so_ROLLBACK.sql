-- Rollback V345: cabut flag conversational_ship_page_so dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_ship_page_so';
