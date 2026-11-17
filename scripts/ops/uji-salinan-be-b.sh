#!/usr/bin/env bash
# BE-B: uji kartu stok campur + isyarat per gudang + riwayat SA di SALINAN (SATU dump milkydb -> klon scratch_beb_<ts>); handler ASLI pohon POHON; tulis hanya di klon; klon DIHAPUS (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-be-b.sh (cek-sibuk dulu). rc 0 = semua cek hijau.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-be-b.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-beb}; S=scratch_beb_$TS; O=$(mktemp -d)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_beb%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_beb%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/beb_$TS.dump" || exit 2
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/beb_$TS.dump >/tmp/beb_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/beb_restore.log)"; docker exec $P rm -f /tmp/beb_$TS.dump
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/beb.json \
  -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$POHON/backend:/app/backend:ro" -v "$D/uji-salinan-be-b.py:/uji.py:ro" -v "$O:/o" \
  -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^\[|Traceback|  File|Error|BUKAN"
echo "rc=${PIPESTATUS[0]} ($POHON @ $(git -C "$POHON" rev-parse --short HEAD))"
