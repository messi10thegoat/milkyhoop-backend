-- Rollback V362: cabut flag halaman Konversi ke pesanan dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_convert_page_quote';
