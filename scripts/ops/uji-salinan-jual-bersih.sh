#!/usr/bin/env bash
# BUG-102/106/103 uji di SALINAN: klon penuh milkydb -> scratch_jb_<ts>; MODE cabang (POHON) lalu MODE dasar (master = kontrol
# merah) atas klon yang sama (baca saja); HAPUS salinan (trap). Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-jual-bersih.sh
set -u
P=milkyhoop-dev-postgres-1; S=scratch_jb_$(date +%s); POHON=${POHON:-/root/mh-jual-bersih}; DASAR=${DASAR:-/root/milkyhoop-dev}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_jb%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_jb%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/jb_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/jb_restore.log); jurnal salinan=$(docker exec $P psql -U postgres -d $S -At -c 'SELECT count(*) FROM journal_entries') prod=$(docker exec $P psql -U postgres -d milkydb -At -c 'SELECT count(*) FROM journal_entries')"
uji() { timeout 600 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "$2/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-jual-bersih.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py "$1" 2>&1 | grep -E "^(PASS|FAIL|HASIL|\[|Traceback|  File)|Error"; }
uji cabang "$POHON"
uji dasar "$DASAR"
