#!/usr/bin/env bash
# Biaya standar SO t1 uji di SALINAN: klon penuh milkydb -> scratch_bya_<ts>; handler ASLI; HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-biaya-t1.sh (cek-sibuk dulu). Tulis HANYA di salinan (T3 idempotensi).
set -u
P=milkyhoop-dev-postgres-1; S=scratch_bya_$(date +%s); POHON=${POHON:-/root/mh-biaya-t1}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_bya%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bya%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bya_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bya_restore.log); jurnal salinan=$(docker exec $P psql -U postgres -d $S -At -c 'SELECT count(*) FROM journal_entries') prod=$(docker exec $P psql -U postgres -d milkydb -At -c 'SELECT count(*) FROM journal_entries')"
uji() { timeout 600 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "$POHON/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-biaya-t1.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(PASS|FAIL|HASIL|\[|Traceback|  File)|Error"; }
uji
