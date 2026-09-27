-- Rollback V321: cabut flag theme_claude dari grapgrap (kaos V319 tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'theme_claude';
