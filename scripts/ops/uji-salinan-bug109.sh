#!/usr/bin/env bash
# BUG-109 uji di SALINAN: klon penuh milkydb -> scratch_bt109_<ts>; V485 diterapkan di salinan; kode cabang atasnya; HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bug109.sh  (cek-sibuk dulu).
set -u
P=milkyhoop-dev-postgres-1; S=scratch_bt109_$(date +%s); W=${POHON:-/root/mh-bug109}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_bt109%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bt109%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bt109_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bt109_restore.log); vc salinan=$(docker exec $P psql -U postgres -d $S -At -c 'SELECT count(*) FROM vendor_credits') prod=$(docker exec $P psql -U postgres -d milkydb -At -c 'SELECT count(*) FROM vendor_credits')"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "$W/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-bug109.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -vE "^\s*$|warn|Warn|INFO|notice" | tail -60
