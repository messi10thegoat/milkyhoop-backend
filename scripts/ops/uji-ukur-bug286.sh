#!/usr/bin/env bash
# BUG-286: UKUR batch ganda di SALINAN (tanpa vonis; hanya angka) (SATU dump milkydb -> klon scratch_b286_<ts>); handler ASLI pohon POHON; tulis hanya di klon; klon DIHAPUS (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-ukur-bug286.sh (cek-sibuk dulu). rc 0 = semua cek hijau.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-ukur-bug286.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-sat}; S=scratch_b286_$TS; O=$(mktemp -d)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_b286%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_b286%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/b286_$TS.dump" || exit 2
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/b286_$TS.dump >/tmp/b286_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/b286_restore.log)"; docker exec $P rm -f /tmp/b286_$TS.dump
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/b286.json -e TEGAS=${TEGAS:-1} \
  -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$POHON/backend:/app/backend:ro" -v "$D/uji-ukur-bug286.py:/uji.py:ro" -v "$O:/o" \
  -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^\[|Traceback|File |Error|BUKAN"
rc=${PIPESTATUS[0]}; echo "($POHON @ $(git -C "$POHON" rev-parse --short HEAD))"; echo "rc=$rc"
