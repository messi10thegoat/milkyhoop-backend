#!/usr/bin/env bash
# Teks Barang uji di SALINAN: klon penuh milkydb -> scratch_bgt_<ts>; tulis HANYA di salinan; HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-barang-teks.sh (cek-sibuk dulu).
set -u
python3 "$(dirname "$0")/cek-saring-tenant.py" "$0" "$(dirname "$0")/uji-salinan-barang-teks.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_bgt_$(date +%s); POHON=${POHON:-/root/mh-barang-teks}
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null
          echo "salinan $S dihapus; sisa DB scratch_bgt%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_bgt%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/bgt_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/bgt_restore.log)"
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} \
  -v "$POHON/backend:/app/backend:ro" -v "$(cd "$(dirname "$0")" && pwd)/uji-salinan-barang-teks.py:/uji.py:ro" -w /app/backend/api_gateway \
  --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(PASS|FAIL|HASIL|TOLAK|INFO|\[|Traceback|  File)|Error"
echo "rc=${PIPESTATUS[0]}"
