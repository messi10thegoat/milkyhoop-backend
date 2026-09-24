#!/usr/bin/env bash
# Gerbang V303 (#10b-3a): fungsi inti AR/AP bertanggal bisnis tenant.
# Membangun DB scratch milkydb_t10b3a = skema milkydb + baris "Tenant" (TIDAK menulis ke milkydb),
# opsional menerapkan migrasi (--migrasi <berkas.sql>), lalu menjalankan scripts/gate_t10b3a_v303.sql.
# Tanpa --migrasi = KONTROL MERAH (definisi prod) -> harus GAGAL. Keluar 0 hanya bila LULUS.
set -uo pipefail
P=milkyhoop-dev-postgres-1
DB=milkydb_t10b3a
DIR="$(cd "$(dirname "$0")" && pwd)"
q() { docker exec -e PGPASSWORD=Proyek771977 "$P" psql -U postgres "$@"; }

q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec -e PGPASSWORD=Proyek771977 "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec -e PGPASSWORD=Proyek771977 "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
if [ "${1:-}" = "--migrasi" ]; then
  docker cp "$2" "$P:/tmp/t10b3a_mig.sql" && q -d "$DB" -v ON_ERROR_STOP=1 -q -1 -f /tmp/t10b3a_mig.sql || { echo "migrasi GAGAL"; exit 2; }
fi
docker cp "$DIR/gate_t10b3a_v303.sql" "$P:/tmp/gate_t10b3a_v303.sql"
q -d "$DB" -q -f /tmp/gate_t10b3a_v303.sql > /tmp/gate_t10b3a.log 2>&1
rc=$?
grep -E "LULUS|GAGAL|ERROR" /tmp/gate_t10b3a.log
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
exit $rc
