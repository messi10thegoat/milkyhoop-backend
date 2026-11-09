-- Rollback V492: matikan 6 flag kontak di grapgrap (rupa lama kembali; tanpa restart).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$';
