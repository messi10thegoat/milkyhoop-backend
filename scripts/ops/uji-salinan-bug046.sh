#!/usr/bin/env bash
# BUG-046 uji di SALINAN: klon penuh milkydb -> scratch_bt046_<ts>; jalankan kode cabang atasnya; HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bug021.sh  (cek-sibuk dulu). Turunan uji-salinan-bug021.sh (7 Nov 2026).
set -u
P=milkyhoop-dev-postgres-1; S=scratch_bt046_$(date +%s)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_bt046%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bt046%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bt046_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bt046_restore.log); produk salinan=$(docker exec $P psql -U postgres -d $S -At -c 'SELECT count(*) FROM products') prod=$(docker exec $P psql -U postgres -d milkydb -At -c 'SELECT count(*) FROM products')"
timeout 600 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-bayar-tunai}/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-bug046.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -vE "^\s*$|warn|Warn|INFO|notice" | tail -60
