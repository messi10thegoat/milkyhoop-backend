#!/usr/bin/env bash
# PEMBACA LAMPIRAN Kas&Bank + Kasbon uji di SALINAN: SATU dump milkydb -> SATU klon (scratch_lampiran_kb_<ts>); lampiran uji ditulis HANYA di klon
# (SIAPKAN=1, pohon LAMA), lalu pohon LAMA dan BARU membaca klon yang SAMA. Banding: jenis lama identik + cek baru (D, X, P). HAPUS klon (trap).
# Pakai: POHON=/root/<worktree> LAMA=/root/<worktree-lama> scripts/ops/uji-salinan-lampiran-kasbank.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-lampiran-kasbank.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-lamp}; LAMA=${LAMA:-/root/milkyhoop-dev}; O=$(mktemp -d); S=scratch_lampiran_kb_$TS
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_lampiran_kb%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_lampiran_kb%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/lampiran_kb_$TS.dump" || exit 2
echo "salinan: $S"
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/lampiran_kb_$TS.dump >/tmp/lampiran_kb_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/lampiran_kb_restore.log)"
docker exec $P rm -f /tmp/lampiran_kb_$TS.dump
for t in lama baru; do
  [ $t = lama ] && SRC=$LAMA SIAP=1 || SRC=$POHON SIAP=0
  timeout 1200 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/$t.json -e SIAPKAN=$SIAP \
    -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$SRC/backend:/app/backend:ro" -v "$D/uji-salinan-lampiran-kasbank.py:/uji.py:ro" -v "$O:/o" \
    -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^(\[|Traceback|  File)|Error|BUKAN"
  echo "$t rc=${PIPESTATUS[0]} ($SRC @ $(git -C $SRC rev-parse --short HEAD))"
done
python3 "$D/banding-lampiran-kasbank.py" "$O/lama.json" "$O/baru.json"
echo "rc=$?"
