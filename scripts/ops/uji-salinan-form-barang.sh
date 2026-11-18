#!/usr/bin/env bash
# FORM BARANG CW: stok awal per gudang + form-bawaan + pratinjau Ubah di SALINAN (SATU dump milkydb -> klon scratch_fb_<ts>); handler ASLI pohon POHON; tulis hanya di klon; klon DIHAPUS (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-form-barang.sh (cek-sibuk dulu). rc 0 = semua cek hijau.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-form-barang.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-fb}; S=scratch_fb_$TS; O=$(mktemp -d)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_fb%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_fb%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/fb_$TS.dump" || exit 2
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/fb_$TS.dump >/tmp/fb_restore.log 2>&1
echo "restore galat: $(grep -c ERROR /tmp/fb_restore.log)"; docker exec $P rm -f /tmp/fb_$TS.dump
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/fb.json  \
  -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$POHON/backend:/app/backend:ro" -v "$D/uji-salinan-form-barang.py:/uji.py:ro" -v "$O:/o" \
  -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^\[|Traceback|File |Error|BUKAN"
rc=${PIPESTATUS[0]}; echo "($POHON @ $(git -C "$POHON" rev-parse --short HEAD))"; echo "rc=$rc"
