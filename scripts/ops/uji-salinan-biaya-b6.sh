#!/usr/bin/env bash
# Biaya B6 uji di SALINAN: SATU dump milkydb -> SATU klon (scratch_biaya_b6_<ts>); pohon LAMA dan BARU membaca klon yang SAMA (baca saja);
# banding: BARU == LAMA kecuali anchor.vendor (== kolom DB) + paper A4. HAPUS klon (trap). Pakai: POHON=/root/<worktree> LAMA=/root/<lama>.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-biaya-b6.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-biaya-b6-r3}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d); S=scratch_biaya_b6_$TS
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_biaya_b6%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_biaya_b6%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/biaya_b6_$TS.dump" || exit 2
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/biaya_b6_$TS.dump >/tmp/biaya_b6_restore.log 2>&1
echo "salinan: $S; restore galat: $(grep -c ERROR /tmp/biaya_b6_restore.log)"; docker exec $P rm -f /tmp/biaya_b6_$TS.dump
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA || SRC=$POHON
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/$t.json \
    -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-biaya-b6.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(bundel|Traceback|  File)|Error"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-biaya-b6.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
