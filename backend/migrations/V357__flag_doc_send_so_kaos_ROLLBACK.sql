-- Rollback V357: cabut flag conversational_doc_send_so dari kaos (kartu Kirim hilang dari panel; tautan yang sudah
-- dibagikan tetap berlaku sampai dicabut/kedaluwarsa).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_doc_send_so';
