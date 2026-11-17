#!/usr/bin/env bash
# DRAF (17 Nov 2026, BELUM PERNAH DIJALANKAN). BUG-276 impor ketik satuan tunggal uji di SALINAN: klon penuh milkydb -> scratch_b276_<ts>; IB.rencana ASLI (baca-saja) atas klon; HAPUS salinan (trap). rc= dari skrip uji. Pakai: POHON=/root/mh-b276 scripts/ops/uji-salinan-b276.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-b276.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_b276_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_b276%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b276%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/b276_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b276_restore.log)"
# Kode paket gabungan membaca kolom V498 (is_default) dan V502 (merek) lewat jalur buat barang: terapkan DI SALINAN (aditif, idempoten)
for m in V498__item_categories_standar_so V502__products_merek; do
  docker exec -i $P psql -q -v ON_ERROR_STOP=1 -U postgres -d $S < "${POHON:-/root/mh-b276}/backend/migrations/$m.sql" >/tmp/b276_mig.log 2>&1 || { echo "$m galat"; cat /tmp/b276_mig.log; exit 2; }
done
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-b276}/backend:/app/backend:ro" -v "$D/uji-salinan-b276.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
