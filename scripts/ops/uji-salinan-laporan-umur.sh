#!/usr/bin/env bash
# LAPORAN UMUR gelombang 4 L2 uji di SALINAN: SATU dump milkydb -> DUA klon (scratch_lap_umur_lama_<ts>, scratch_lap_umur_baru_<ts>); aging-receivable+payable lama vs baru
# ASLI pohon LAMA di klon lama, pohon BARU di klon baru (tulis hanya di klon). Banding: hanya perubahan yang diputuskan. HAPUS klon (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-laporan-umur.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-laporan-umur.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-umur}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d)
hapus() { for t in lama baru; do docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS scratch_lap_umur_${t}_$TS" >/dev/null; done; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_lap_umur%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_lap_umur%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/lap_umur_$TS.dump" || exit 2
for t in lama baru; do
  S=scratch_lap_umur_${t}_$TS; echo "salinan: $S"
  docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
  docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/lap_umur_$TS.dump >/tmp/lap_umur_restore_$t.log 2>&1
  echo "restore $t galat: $(grep -c ERROR /tmp/lap_umur_restore_$t.log)"
done
docker exec $P rm -f /tmp/lap_umur_$TS.dump
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA || SRC=$POHON
  timeout 1200 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=scratch_lap_umur_${t}_$TS -e KELUAR=/o/$t.json \
    -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-laporan-umur.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error|BUKAN"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-laporan-umur.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
