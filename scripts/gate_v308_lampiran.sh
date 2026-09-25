#!/usr/bin/env bash
# Gerbang V308 (#30): trigger lepas tautan dokumen saat entitas dihapus-keras.
# DB scratch milkydb_v308 = skema milkydb + baris "Tenant" (TIDAK menulis ke milkydb).
#   gate_v308_lampiran.sh --migrasi <V308.sql>   -> harus LULUS (exit 0)
#   gate_v308_lampiran.sh                        -> KONTROL MERAH (tanpa migrasi) -> harus GAGAL
set -uo pipefail
P=milkyhoop-dev-postgres-1
DB=milkydb_v308
DIR="$(cd "$(dirname "$0")" && pwd)"
q() { docker exec "$P" psql -U postgres "$@"; }

q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
if [ "${1:-}" = "--migrasi" ]; then
  docker cp "$2" "$P:/tmp/v308_mig.sql" && q -d "$DB" -v ON_ERROR_STOP=1 -q -1 -f /tmp/v308_mig.sql || { echo "migrasi GAGAL"; q -d postgres -qc "DROP DATABASE IF EXISTS $DB"; exit 2; }
fi
docker cp "$DIR/gate_v308_lampiran.sql" "$P:/tmp/gate_v308_lampiran.sql"
q -d "$DB" -q -f /tmp/gate_v308_lampiran.sql > /tmp/gate_v308.log 2>&1
rc=$?
grep -E "LULUS|GAGAL|ERROR" /tmp/gate_v308.log
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
exit $rc
