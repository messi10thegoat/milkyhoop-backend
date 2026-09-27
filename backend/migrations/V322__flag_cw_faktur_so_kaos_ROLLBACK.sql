-- Rollback V322: cabut flag conversational_invoice_page_so dari kaos (FE kembali ke tema lama).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_invoice_page_so';
