#!/usr/bin/env bash
# DRAF (17 Nov 2026, BELUM PERNAH DIJALANKAN). BUG-261 uji di SALINAN: klon penuh milkydb -> scratch_b261_<ts>; V503 diterapkan 2x di klon; penulis ASLI (pemindahan kirim/terima, penyesuaian buat/posting) dalam transaksi di-ROLLBACK; HAPUS salinan (trap). rc= dari skrip uji. Pakai: POHON=/root/mh-b261 scripts/ops/uji-salinan-b261.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-b261.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_b261_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_b261%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b261%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/b261_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b261_restore.log)"
# Dasar paket-be-all (kode membaca V498/V502 di jalur lain): terapkan juga di salinan (aditif, idempoten)
for m in V498__item_categories_standar_so V502__products_merek; do
  docker exec -i $P psql -q -v ON_ERROR_STOP=1 -U postgres -d $S < "${POHON:-/root/mh-b261}/backend/migrations/$m.sql" >/tmp/b261_mig0.log 2>&1 || { echo "$m galat"; cat /tmp/b261_mig0.log; exit 2; }
done
# V503 DI SALINAN (aditif; diterapkan 2x = idempoten). Pembungkus ini bukan prod: apply_mig prod = MASTER saat jendela.
for i in 1 2; do
  docker exec -i $P psql -q -v ON_ERROR_STOP=1 -U postgres -d $S < "${POHON:-/root/mh-b261}/backend/migrations/V503__penyesuaian_gudang_baris_sumber_pemindahan.sql" >/tmp/b261_mig_$i.log 2>&1 || { echo "V503 galat (jalan $i)"; cat /tmp/b261_mig_$i.log; exit 2; }
done
echo "V503 diterapkan 2x di salinan (idempoten)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-b261}/backend:/app/backend:ro" -v "$D/uji-salinan-b261.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
