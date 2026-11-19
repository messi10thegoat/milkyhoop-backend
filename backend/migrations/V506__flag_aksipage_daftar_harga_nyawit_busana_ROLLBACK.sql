-- Rollback V506: matikan aksipage_daftar_harga_v1 di nyawit + busana (kaos tetap; aksi Daftar Harga lama PERSIS kembali; tanpa restart).
DELETE FROM tenant_features WHERE tenant_id IN ('pt-kita-nyawit-bersama', 'pt-busana-indah-semesta') AND feature = 'aksipage_daftar_harga_v1';
