#!/usr/bin/env bash
# DRAF (16 Nov 2026, BELUM PERNAH DIJALANKAN -- ditulis di Mac tanpa akses server; jalankan pertama kali di slot, perbaiki seperlunya).
# BUG-249 nama pelanggan di Cek/Giro + Biaya (customers.nama, bukan customers.name NULL), uji di SALINAN: klon penuh milkydb -> scratch_b249_<ts>; handler ASLI; HAPUS salinan (trap).
# Pakai: POHON=/root/mh-b249 scripts/ops/uji-salinan-b249.sh  (cek-sibuk dulu). Turunan uji-salinan-b230.sh. rc= dari skrip uji.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-b249.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_b249_$(date +%s); O=$(mktemp); POHON=${POHON:-/root/mh-b249}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_b249%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b249%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/b249_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b249_restore.log)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "$POHON/backend:/app/backend:ro" -v "$D/uji-salinan-b249.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
