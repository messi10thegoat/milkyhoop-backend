#!/usr/bin/env bash
# BF-1e uji di SALINAN: klon penuh milkydb -> scratch_bf1e_<ts>; satu pohon (BARU = POHON; tanpa ekstraksi); HAPUS salinan (trap). Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bankfeed-1e.sh (cek-sibuk dulu).
set -u
D="$(dirname "$0")"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-bankfeed-1e.py" "$D/uji_c3_tanda.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_bf1e_$(date +%s); POHON=${POHON:-/root/mh-bf1e}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_bf1e%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bf1e%'")"; }
trap hapus EXIT
echo "salinan: $S; BARU=$(git -C "$POHON" rev-parse --short HEAD)"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bf1e_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bf1e_restore.log)"
ENVF=/root/milkyhoop-dev/.env
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file $ENVF -e DB_NAME=$S -v "$POHON/backend:/app/backend:ro" \
  -v "$(cd "$D" && pwd)/uji_c3_tanda.py:/tanda/uji_c3_tanda.py:ro" -v "$(cd "$D" && pwd)/uji-salinan-bankfeed-1e.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(PASS|FAIL|INFO|HASIL|TOLAK|\[|Traceback|  File)|Error|error"
echo "rc=${PIPESTATUS[0]}"
