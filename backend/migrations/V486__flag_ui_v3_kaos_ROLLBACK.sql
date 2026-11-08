-- ROLLBACK V486: matikan flag ui_v3 di kaos (nol jurnal; FE kembali ke tampilan lama <=60 dtk fokus / muat ulang).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'ui_v3';
