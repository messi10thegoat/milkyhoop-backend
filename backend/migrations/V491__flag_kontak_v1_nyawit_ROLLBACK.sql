-- Rollback V491: matikan 6 flag kontak di nyawit (rupa lama kembali; tanpa restart).
DELETE FROM tenant_features WHERE tenant_id = 'pt-kita-nyawit-bersama' AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$';
