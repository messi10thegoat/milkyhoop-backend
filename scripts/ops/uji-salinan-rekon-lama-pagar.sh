#!/usr/bin/env bash
# REKON-LAMA-PAGAR uji di SALINAN: SATU dump milkydb -> SATU klon (scratch_rekon_lp_<ts>); sesi rekonsiliasi diselesaikan HANYA di klon;
# pohon BARU lalu LAMA pada klon yang SAMA. Banding: cek baru + bukti lubang lama. HAPUS klon (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-rekon-lama-pagar.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-rekon-lama-pagar.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-rekonlama}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d); S=scratch_rekon_lp_$TS
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_rekon_lp%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_rekon_lp%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/rekon_lp_$TS.dump" || exit 2
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/rekon_lp_$TS.dump >/tmp/rekon_lp_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/rekon_lp_restore.log)"
docker exec $P rm -f /tmp/rekon_lp_$TS.dump
for t in baru lama; do  # BARU dulu: LAMA bisa menyelesaikan sesi (menulis) di klon
  [ $t = lama ] && SRC=$LAMA SIAP=1 || SRC=$POHON SIAP=0
  timeout 1200 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/$t.json -e SIAPKAN=$SIAP \
    -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-rekon-lama-pagar.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error|BUKAN"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-rekon-lama-pagar.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
