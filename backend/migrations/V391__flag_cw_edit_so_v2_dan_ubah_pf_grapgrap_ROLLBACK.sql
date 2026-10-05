-- Rollback V391: cabut 2 flag edit dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_edit_so_v2', 'conversational_edit_page_pf');
