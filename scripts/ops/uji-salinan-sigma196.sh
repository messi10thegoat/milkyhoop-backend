#!/usr/bin/env bash
# BUG-196 uji di SALINAN: klon penuh milkydb -> scratch_bsigma196_<ts>; prep (baris tak-berlaku uji, HANYA klon); rekam sum_total 13 daftar dgn kode DASAR (DASAR=/root/mh-dasar196) lalu kode CABANG (POHON); bandingkan. rc= dari pembanding.
# Pakai: POHON=/root/mh-b196 DASAR=/root/mh-dasar196 scripts/ops/uji-salinan-sigma196.sh  (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-sigma196.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_bsigma196_$(date +%s); B=$(mktemp); A=$(mktemp)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -f "$B" "$A"
          echo "salinan $S dihapus; sisa DB scratch_bsigma196%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bsigma196%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bsigma196_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bsigma196_restore.log)"
jalan() {  # $1 pohon kode, $2.. mode
  local pohon="$1"; shift
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S \
    -v "$pohon/backend:/app/backend:ro" -v "$D/uji-salinan-sigma196.py:/uji.py:ro" -w /app/backend/api_gateway \
    --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py "$@" 2>&1 | grep -E "^(PREP|SUM|EXCL|Traceback|.*Error)" 
}
jalan "${POHON:-/root/mh-b196}" prep
jalan "${DASAR:-/root/mh-dasar196}" rekam > "$B"
jalan "${POHON:-/root/mh-b196}" rekam > "$A"
echo "-- sebelum (dasar):"; grep -c "^SUM" "$B"; echo "-- sesudah (cabang):"; grep -c "^SUM" "$A"
python3 "$D/uji-salinan-sigma196.py" bandingkan "$B" "$A"; RC=$?
echo "rc=$RC"
exit $RC
