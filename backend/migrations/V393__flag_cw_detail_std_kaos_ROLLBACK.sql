-- Rollback V393: cabut 5 flag detail_std dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_detail_std_dp', 'conversational_detail_std_pf', 'conversational_detail_std_cn', 'conversational_detail_std_si', 'conversational_detail_std_quote');
