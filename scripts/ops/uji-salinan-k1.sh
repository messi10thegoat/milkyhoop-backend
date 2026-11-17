#!/usr/bin/env bash
# DRAF (17 Nov 2026, BELUM PERNAH DIJALANKAN -- ditulis di Mac tanpa akses server; jalankan pertama kali di slot, perbaiki seperlunya).
# K-1 daftar standar master Kategori barang uji di SALINAN: klon penuh milkydb -> scratch_k1_<ts>; service ASLI; HAPUS salinan (trap). rc= dari skrip uji. Pakai: POHON=/root/mh-k1 scripts/ops/uji-salinan-k1.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-k1.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_k1_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_k1%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_k1%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/k1_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/k1_restore.log)"
# V498 DI SALINAN (aditif; diterapkan 2x = idempoten); kode K-1/K-2 membaca kolom barunya
POHON=${POHON:-/root/mh-k1}
for i in 1 2; do docker exec -i $P psql -q -v ON_ERROR_STOP=1 -U postgres -d $S < "$POHON/backend/migrations/V498__item_categories_standar_so.sql" >/tmp/k1_v498_$i.log 2>&1 || { echo "V498 galat (jalan $i)"; cat /tmp/k1_v498_$i.log; exit 2; }; done
echo "V498 diterapkan 2x di salinan (idempoten)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-k1}/backend:/app/backend:ro" -v "$D/uji-salinan-k1.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
