#!/usr/bin/env bash
# Biaya BE K8 uji di SALINAN: klon penuh milkydb -> scratch_bk8_<ts>; jalankan kode cabang atasnya; HAPUS salinan (trap). rc= dari skrip uji.
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-k8.sh  (cek-sibuk dulu). Turunan uji-salinan-nol212.sh.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-k8.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_bk8_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_bk8%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bk8%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bk8_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bk8_restore.log)"
MIG="${POHON:-/root/mh-bk8}/backend/migrations"
for i in 1 2; do cat "$MIG/V497__warehouses_is_system.sql" | docker exec -i $P psql -q -U postgres -d $S -v ON_ERROR_STOP=1 >/tmp/bk8_mig.log 2>&1; echo "V497 terapan di salinan ($i, idempoten): rc=$?"; done
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-bk8}/backend:/app/backend:ro" -v "$D/uji-salinan-k8.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
cat "$MIG/V497__warehouses_is_system_ROLLBACK.sql" | docker exec -i $P psql -q -U postgres -d $S -v ON_ERROR_STOP=1 >/dev/null 2>&1
SISA=$(docker exec $P psql -U postgres -d $S -At -c "/* tenant_id */ SELECT count(*) FROM information_schema.columns WHERE table_name = 'warehouses' AND column_name = 'is_system'")
cat "$MIG/V497__warehouses_is_system.sql" | docker exec -i $P psql -q -U postgres -d $S -v ON_ERROR_STOP=1 >/dev/null 2>&1
ADA=$(docker exec $P psql -U postgres -d $S -At -c "/* tenant_id */ SELECT count(*) FROM information_schema.columns WHERE table_name = 'warehouses' AND column_name = 'is_system'")
[ "$SISA" = "0" ] && [ "$ADA" = "1" ] && echo "PASS ROLLBACK V497 membuang kolom (sisa=$SISA) dan terapan ulang memulihkannya (ada=$ADA)" || { echo "FAIL ROLLBACK V497 sisa=$SISA ada=$ADA"; RC=1; }
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
