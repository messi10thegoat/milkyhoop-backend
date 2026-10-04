-- Rollback V371: cabut flag ubah SO terkonfirmasi dari kaos (BE kembali menolak ubah SO non-draf).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'so_edit_confirmed';
