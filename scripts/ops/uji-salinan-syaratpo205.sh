#!/usr/bin/env bash
# Biaya BE BUG-205 uji di SALINAN: klon penuh milkydb -> scratch_bsyaratpo205_<ts>; jalankan kode cabang atasnya; HAPUS salinan (trap). rc= dari skrip uji.
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-syaratpo205.sh  (cek-sibuk dulu). Turunan uji-salinan-biaya-gel1.sh.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-syaratpo205.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_bsyaratpo205_$(date +%s); O=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$O"
          echo "salinan $S dihapus; sisa DB scratch_bsyaratpo205%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bsyaratpo205%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bsyaratpo205_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bsyaratpo205_restore.log)"
docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 < "${POHON:-/root/mh-b205}/backend/migrations/V496__po_payment_terms_days.sql" > /tmp/bsyaratpo205_mig.log 2>&1; echo "migrasi V496 di SALINAN: rc=$? $(tail -1 /tmp/bsyaratpo205_mig.log)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -v "${POHON:-/root/mh-b205}/backend:/app/backend:ro" -v "$D/uji-salinan-syaratpo205.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -80
echo "rc=$RC"
exit $RC
