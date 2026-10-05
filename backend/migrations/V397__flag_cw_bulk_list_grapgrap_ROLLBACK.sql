-- Rollback V397: cabut flag bulk_list dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_bulk_list';
