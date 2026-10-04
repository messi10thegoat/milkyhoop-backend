-- Rollback V373: cabut flag ubah SO terkonfirmasi dari grapgrap (BE kembali menolak ubah SO non-draf).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'so_edit_confirmed';
