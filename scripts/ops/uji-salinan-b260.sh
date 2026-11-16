#!/usr/bin/env bash
# DRAF (16 Nov 2026, BELUM PERNAH DIJALANKAN -- tulis di Mac tanpa akses server; jalankan pertama kali di slot, perbaiki seperlunya).
# BUG-260 BE + V502 merek, uji di SALINAN: klon penuh milkydb -> scratch_b260_<ts>; terapkan V502 di SALINAN (bukan prod); jalankan handler ASLI atas klon; HAPUS salinan (trap).
# Pakai: POHON=/root/mh-b260 scripts/ops/uji-salinan-b260.sh  (cek-sibuk dulu). Turunan uji-salinan-b230.sh. rc= dari skrip uji.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-b260.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_b260_$(date +%s); O=$(mktemp); POHON=${POHON:-/root/mh-b260}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_b260%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b260%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/b260_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b260_restore.log)"
# V502 DI SALINAN (migrasi aditif); diulang sekali = harus idempoten (IF NOT EXISTS)
for i in 1 2; do docker exec -i $P psql -q -v ON_ERROR_STOP=1 -U postgres -d $S < "$POHON/backend/migrations/V502__products_merek.sql" >/tmp/b260_v502_$i.log 2>&1 || { echo "V502 galat (jalan $i)"; cat /tmp/b260_v502_$i.log; exit 2; }; done
echo "V502 diterapkan 2x di salinan (idempoten)"
timeout 1500 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "$POHON/backend:/app/backend:ro" -v "$D/uji-salinan-b260.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -120
echo "rc=$RC"
exit $RC
