#!/usr/bin/env bash
# aksi_tersedia DETAIL SO (gelombang 3 J1a) uji di SALINAN: SATU dump milkydb -> DUA klon (scratch_so_aksi_lama_<ts>, scratch_so_aksi_baru_<ts>); handler SO + penulis aksi
# ASLI pohon LAMA di klon lama, pohon BARU di klon baru (tulis hanya di klon). Banding: hanya perubahan yang diputuskan. HAPUS klon (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-so-aksi.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-so-aksi.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-jual}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d)
hapus() { for t in lama baru; do docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS scratch_so_aksi_${t}_$TS" >/dev/null; done; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_so_aksi%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_so_aksi%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/so_aksi_$TS.dump" || exit 2
for t in lama baru; do
  S=scratch_so_aksi_${t}_$TS; echo "salinan: $S"
  docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
  docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/so_aksi_$TS.dump >/tmp/so_aksi_restore_$t.log 2>&1
  echo "restore $t galat: $(grep -c ERROR /tmp/so_aksi_restore_$t.log)"
done
docker exec $P rm -f /tmp/so_aksi_$TS.dump
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA || SRC=$POHON
  timeout 2400 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=scratch_so_aksi_${t}_$TS -e KELUAR=/o/$t.json \
    -e SAMPEL=${SAMPEL:-15} -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-so-aksi.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error|BUKAN"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-so-aksi.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
