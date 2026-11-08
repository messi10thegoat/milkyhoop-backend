#!/usr/bin/env bash
# ROW (v) BUG-147 + BUG-136 uji di SALINAN: klon milkydb -> scratch_rov_v_<ts>; handler GRN + NK ASLI dari pohon LAMA (deploy/master) dan
# BARU (cabang) di salinan yang SAMA; banding: hanya perubahan yang diputuskan. Baca saja. HAPUS salinan (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-rov-v.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-rov-v.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; S=scratch_rov_v_$(date +%s); POHON=${POHON:-/root/mh-rov-v}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan $S dihapus; sisa DB scratch_rov_v%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_rov_v%'")"; }
trap hapus EXIT
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl | psql -q -U postgres -d $S" >/tmp/rov_v_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/rov_v_restore.log)"
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA || SRC=$POHON
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/$t.json \
    -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-rov-v.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-rov-v.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
