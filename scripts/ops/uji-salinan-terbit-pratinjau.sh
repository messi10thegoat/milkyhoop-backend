#!/usr/bin/env bash
# TERBIT-PRATINJAU-1 uji di SALINAN: SATU dump milkydb -> SATU klon (scratch_terbit_pv_<ts>); stok draf disetel HANYA di klon
# (SIAPKAN=1, pohon LAMA), lalu pohon LAMA dan BARU membaca klon yang SAMA. Banding: jenis lama identik + cek baru (E, J, K) + L. HAPUS klon (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-terbit-pratinjau.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-terbit-pratinjau.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-terbit}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d); S=scratch_terbit_pv_$TS
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_terbit_pv%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_terbit_pv%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/terbit_pv_$TS.dump" || exit 2
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/terbit_pv_$TS.dump >/tmp/terbit_pv_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/terbit_pv_restore.log)"
docker exec $P rm -f /tmp/terbit_pv_$TS.dump
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA SIAP=1 || SRC=$POHON SIAP=0
  timeout 1200 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/$t.json -e SIAPKAN=$SIAP \
    -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-terbit-pratinjau.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error|BUKAN"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-terbit-pratinjau.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
