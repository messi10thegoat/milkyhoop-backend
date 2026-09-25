#!/usr/bin/env bash
# Gerbang W0 (BE): idempotency POST /api/sales-orders + flag fitur /api/permissions/me.
# DB scratch milkydb_w0 = skema milkydb + baris "Tenant" + pelanggan kaos & grapgrap (TIDAK
# menulis ke milkydb), lalu V304; handler dijalankan in-process di kontainer sekali-pakai
# (image gateway, pohon <worktree> di-mount, TANPA env prod). Keluar 0 hanya bila LULUS.
# Pemakaian: gate_w0.sh <worktree> [--tanpa-v304]
set -uo pipefail
PGPW=$(grep -E "^DB_PASSWORD=" /root/milkyhoop-dev/.env | cut -d= -f2-)  # #41: dari .env, bukan literal
[ -n "$PGPW" ] || { echo "GALAT ALAT: DB_PASSWORD kosong"; exit 2; }
POHON=${1:?worktree}; P=milkyhoop-dev-postgres-1; DB=milkydb_w0
DIR="$(cd "$(dirname "$0")" && pwd)"
q() { docker exec "$P" psql -U postgres "$@"; }
qi() { docker exec -i "$P" psql -U postgres "$@"; }  # -i: stdin (COPY FROM)
q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
# Daftar kolom EKSPLISIT dari scratch (urutan fisik prod bisa beda karena kolom yang di-drop/ditambah).
KOL=$(q -d "$DB" -qAtc "SELECT string_agg(quote_ident(column_name), ',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='customers' AND is_generated='NEVER'")
[ -n "$KOL" ] || { echo "GALAT ALAT: tabel customers tak ada di scratch"; exit 2; }
q -d milkydb -qAtc "COPY (SELECT $KOL FROM customers WHERE tenant_id IN ('kaos-biru-konveksi','grapgrap-manado')) TO STDOUT" \
  | qi -d "$DB" -v ON_ERROR_STOP=1 -qc "COPY customers ($KOL) FROM STDIN" || { echo "GALAT ALAT: salin pelanggan"; exit 2; }
if [ "${2:-}" != "--tanpa-v304" ]; then
  docker cp "$POHON/backend/migrations/V304__tenant_features.sql" "$P:/tmp/w0_v304.sql" \
    && q -d "$DB" -v ON_ERROR_STOP=1 -q -1 -f /tmp/w0_v304.sql || { echo "V304 GAGAL"; exit 2; }
fi
docker run --rm --network milkyhoop_dev_network \
  -v "$POHON/backend/api_gateway:/app/backend/api_gateway:ro" -v "$DIR/gate_w0.py:/gate_w0.py:ro" \
  -w /app/backend/api_gateway -e PYTHONPATH=/app/backend/api_gateway:/app \
  -e W0_DB=$DB -e W0_DSN="postgresql://postgres:$PGPW@postgres:5432/$DB" \
  --entrypoint python milkyhoop-dev-api_gateway:latest /gate_w0.py 2>&1 | grep -E "^(LULUS|GAGAL|GALAT|RINGKAS)|Error|Traceback" 
rc=${PIPESTATUS[0]}
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
exit $rc
