-- Rollback V365: cabut flag tawaran konfirmasi saat Terima DP dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_dp_confirm_offer_so';
