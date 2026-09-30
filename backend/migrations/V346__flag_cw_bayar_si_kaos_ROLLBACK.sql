-- Rollback V346: cabut flag conversational_payment_page_si dari kaos (FE kembali ke tema lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_payment_page_si';
