-- ROLLBACK V487: matikan flag ui_v3 di nyawit (nol jurnal; FE kembali ke tampilan lama <=60 dtk fokus / muat ulang). kaos (V486) tak disentuh.
DELETE FROM tenant_features WHERE tenant_id = 'pt-kita-nyawit-bersama' AND feature = 'ui_v3';
