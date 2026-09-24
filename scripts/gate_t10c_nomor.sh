#!/usr/bin/env bash
# Gerbang #10c (V302): nomor dokumen/jurnal ikut tanggal bisnis tenant.
# Membangun DB scratch milkydb_t10c = skema milkydb + baris "Tenant" (TIDAK menulis ke milkydb),
# opsional menerapkan migrasi (--migrasi <berkas.sql>), lalu menjalankan scripts/gate_t10c_nomor.sql.
# Tanpa --migrasi = KONTROL MERAH (definisi prod) -> harus GAGAL. Keluar 0 hanya bila LULUS.
set -uo pipefail
P=milkyhoop-dev-postgres-1
DB=milkydb_t10c
DIR="$(cd "$(dirname "$0")" && pwd)"
q() { docker exec -e PGPASSWORD=Proyek771977 "$P" psql -U postgres "$@"; }

q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec -e PGPASSWORD=Proyek771977 "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec -e PGPASSWORD=Proyek771977 "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
if [ "${1:-}" = "--migrasi" ]; then
  docker cp "$2" "$P:/tmp/t10c_mig.sql" && q -d "$DB" -v ON_ERROR_STOP=1 -q -1 -f /tmp/t10c_mig.sql || { echo "migrasi GAGAL"; exit 2; }
fi
docker cp "$DIR/gate_t10c_nomor.sql" "$P:/tmp/gate_t10c_nomor.sql"
q -d "$DB" -q -f /tmp/gate_t10c_nomor.sql > /tmp/gate_t10c.log 2>&1
rc=$?
grep -E "LULUS|GAGAL|ERROR" /tmp/gate_t10c.log
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
exit $rc
