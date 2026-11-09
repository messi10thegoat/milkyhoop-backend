-- ROLLBACK V488: matikan flag ui_v3 di grapgrap (nol jurnal; FE kembali ke tampilan lama <=60 dtk fokus / muat ulang). kaos/nyawit tak disentuh.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'ui_v3';
