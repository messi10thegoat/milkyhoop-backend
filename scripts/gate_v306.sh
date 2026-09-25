#!/usr/bin/env bash
# Gerbang V306 (backlog 3b): 15 fungsi DB bertanggal bisnis tenant (pemindai + perilaku zona sesi).
# Membangun DB scratch milkydb_v306 = skema milkydb + baris "Tenant" (TIDAK menulis ke milkydb),
# opsional menerapkan migrasi (--migrasi <berkas.sql>), lalu menjalankan scripts/gate_v306.sql.
# Tanpa --migrasi = KONTROL MERAH (definisi prod) -> harus GAGAL. Keluar 0 hanya bila LULUS.
set -uo pipefail
P=milkyhoop-dev-postgres-1
DB=milkydb_v306
DIR="$(cd "$(dirname "$0")" && pwd)"
q() { docker exec "$P" psql -U postgres "$@"; }

q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
if [ "${1:-}" = "--migrasi" ]; then
  docker cp "$2" "$P:/tmp/v306_mig.sql" && q -d "$DB" -v ON_ERROR_STOP=1 -q -1 -f /tmp/v306_mig.sql || { echo "migrasi GAGAL"; exit 2; }
fi
docker cp "$DIR/gate_v306.sql" "$P:/tmp/gate_v306.sql"
q -d "$DB" -q -f /tmp/gate_v306.sql > /tmp/gate_v306.log 2>&1
rc=$?
grep -E "LULUS|GAGAL|ERROR|zona sesi" /tmp/gate_v306.log
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
exit $rc
