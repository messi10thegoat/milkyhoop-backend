\set ON_ERROR_STOP off
\pset border 2

\echo '=== HIJAU-1: dua TENANT beda, key SAMA -> harus DUA BARIS, nol error ==='
BEGIN;
INSERT INTO idempotency_keys (key, tenant_id, source_type, result, expires_at)
VALUES ('RCV:probe', 'tenant-A', 'RECEIVE_PAYMENT', '{}', NOW() + interval '24 hours');
INSERT INTO idempotency_keys (key, tenant_id, source_type, result, expires_at)
VALUES ('RCV:probe', 'tenant-B', 'RECEIVE_PAYMENT', '{}', NOW() + interval '24 hours')
ON CONFLICT (tenant_id, key) DO NOTHING;
SELECT tenant_id, key FROM idempotency_keys WHERE key = 'RCV:probe' ORDER BY tenant_id;

\echo '=== HIJAU-2: tenant SAMA, key SAMA -> ON CONFLICT DO NOTHING bekerja (tetap 1 baris) ==='
INSERT INTO idempotency_keys (key, tenant_id, source_type, result, expires_at)
VALUES ('RCV:probe', 'tenant-A', 'RECEIVE_PAYMENT', '{"kedua":true}', NOW() + interval '24 hours')
ON CONFLICT (tenant_id, key) DO NOTHING;
SELECT count(*) AS baris_tenant_A FROM idempotency_keys
 WHERE key = 'RCV:probe' AND tenant_id = 'tenant-A';
SELECT result AS result_tenant_A_harus_kosong_bukan_kedua FROM idempotency_keys
 WHERE key = 'RCV:probe' AND tenant_id = 'tenant-A';
ROLLBACK;

\echo '=== IDEMPOTEN: jalankan V221 lagi -> harus aman ==='
