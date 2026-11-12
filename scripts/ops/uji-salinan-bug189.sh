#!/usr/bin/env bash
# Biaya BE BUG-189 uji di SALINAN: klon penuh milkydb -> scratch_bbug189_<ts>; jalankan kode cabang atasnya; HAPUS salinan (trap). rc= dari skrip uji.
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-bug189.sh  (cek-sibuk dulu). Turunan uji-salinan-biaya-gel1.sh.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-bug189.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_bbug189_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_bbug189%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bbug189%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bbug189_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bbug189_restore.log)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-bug189}/backend:/app/backend:ro" -v "$D/uji-salinan-bug189.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
