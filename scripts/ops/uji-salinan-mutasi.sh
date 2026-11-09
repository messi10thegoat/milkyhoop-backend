#!/usr/bin/env bash
# MUTASI kontak uji di SALINAN: klon milkydb -> scratch_mutasi_<ts>; handler ASLI pohon cabang; paritas saldo_akhir == saldo detail untuk
# SETIAP kontak kaos/nyawit. Baca saja. HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-mutasi.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-mutasi.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_mutasi_$(date +%s); POHON=${POHON:-/root/mh-kontak-mutasi}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_mutasi%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_mutasi%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/mutasi_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/mutasi_restore.log)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
  -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$POHON/backend:/app/backend:ro" -v "$D/uji-salinan-mutasi.py:/uji.py:ro" \
  -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|FAIL|Traceback|  File|[0-9]+/[0-9]+)|Error|BUKAN"
echo "rc=${PIPESTATUS[0]} ($POHON @ $(git -C $POHON rev-parse --short HEAD))"
