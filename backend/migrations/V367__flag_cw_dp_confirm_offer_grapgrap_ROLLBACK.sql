-- Rollback V367: cabut flag tawaran konfirmasi saat Terima DP dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_dp_confirm_offer_so';
