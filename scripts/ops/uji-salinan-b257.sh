#!/usr/bin/env bash
# DRAF (16 Nov 2026, BELUM PERNAH DIJALANKAN -- ditulis di Mac tanpa akses server; jalankan pertama kali di slot, perbaiki seperlunya).
# BUG-257 histori harga pelanggan uji di SALINAN: klon penuh milkydb -> scratch_b257_<ts>; kode cabang atasnya; HAPUS salinan (trap). rc= dari skrip uji. Pakai: POHON=/root/mh-b257 scripts/ops/uji-salinan-b257.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-b257.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_b257_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_b257%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b257%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/b257_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b257_restore.log)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-b257}/backend:/app/backend:ro" -v "$D/uji-salinan-b257.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
