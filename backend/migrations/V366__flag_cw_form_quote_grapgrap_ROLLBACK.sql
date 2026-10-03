-- Rollback V366: cabut flag form Penawaran CW dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_form_quote';
