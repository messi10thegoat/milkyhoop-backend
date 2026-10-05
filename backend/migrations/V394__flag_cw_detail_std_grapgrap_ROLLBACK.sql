-- Rollback V394: cabut 7 flag detail_std dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_detail_std_dl', 'conversational_detail_std_rp', 'conversational_detail_std_dp', 'conversational_detail_std_pf', 'conversational_detail_std_cn', 'conversational_detail_std_si', 'conversational_detail_std_quote');
