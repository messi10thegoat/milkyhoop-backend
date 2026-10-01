-- Rollback V358: cabut flag conversational_doc_send_so dari grapgrap (kartu Kirim hilang dari panel). Tautan yang sudah
-- dikirim ke pelanggan TETAP berlaku sampai dicabut/kedaluwarsa (30 hari) -- cabut lewat panel bila perlu.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_doc_send_so';
