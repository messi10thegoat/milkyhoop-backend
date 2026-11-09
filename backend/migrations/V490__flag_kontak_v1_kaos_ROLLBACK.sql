-- Rollback V490: matikan 6 flag kontak di kaos (rupa lama kembali; tanpa restart).
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$';
