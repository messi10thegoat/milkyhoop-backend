-- Rollback V337: cabut flag conversational_kasbon_page dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_kasbon_page';
