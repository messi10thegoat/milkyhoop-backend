-- UJI DUA ARAH V221 (Law 33). Dijalankan di CLONE, bukan live.
\set ON_ERROR_STOP off
\pset border 2

\echo '=== BASELINE (sebelum V221): MERAH — lintas tenant harus GAGAL ==='
BEGIN;
INSERT INTO idempotency_keys (key, tenant_id, source_type, result, expires_at)
VALUES ('RCV:probe', 'tenant-A', 'RECEIVE_PAYMENT', '{}', NOW() + interval '24 hours');
INSERT INTO idempotency_keys (key, tenant_id, source_type, result, expires_at)
VALUES ('RCV:probe', 'tenant-B', 'RECEIVE_PAYMENT', '{}', NOW() + interval '24 hours')
ON CONFLICT (tenant_id, key) DO NOTHING;
\echo '^^^ kalau TIDAK ada ERROR di atas, baseline tidak merah -> uji tak sah'
ROLLBACK;
