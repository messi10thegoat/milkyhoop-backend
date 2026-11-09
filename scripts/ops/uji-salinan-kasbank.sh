#!/usr/bin/env bash
# KAS & BANK standar SO uji di SALINAN: SATU dump milkydb -> DUA klon (scratch_kasbank_lama_<ts>, scratch_kasbank_baru_<ts>); handler rekening, transfer, uang masuk/keluar
# ASLI pohon LAMA di klon lama, pohon BARU di klon baru (tulis hanya di klon). Banding: hanya perubahan yang diputuskan. HAPUS klon (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-kasbank.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-kasbank.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-kasbank}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d)
hapus() { for t in lama baru; do docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS scratch_kasbank_${t}_$TS" >/dev/null; done; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_kasbank%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_kasbank%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/kasbank_$TS.dump" || exit 2
for t in lama baru; do
  S=scratch_kasbank_${t}_$TS; echo "salinan: $S"
  docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
  docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/kasbank_$TS.dump >/tmp/kasbank_restore_$t.log 2>&1
  echo "restore $t galat: $(grep -c ERROR /tmp/kasbank_restore_$t.log)"
done
docker exec $P rm -f /tmp/kasbank_$TS.dump
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA || SRC=$POHON
  timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=scratch_kasbank_${t}_$TS -e KELUAR=/o/$t.json \
    -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-kasbank.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error|BUKAN"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-kasbank.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
