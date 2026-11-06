-- ROLLBACK V482: matikan flag CW Impor barang di nyawit SAJA (nol jurnal; barang yang sudah diimpor & flag kaos V481 tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'pt-kita-nyawit-bersama' AND feature = 'conversational_impor_barang';
