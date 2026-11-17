#!/usr/bin/env bash
# KATEGORI-BE: akun bawaan kategori (V505 diterapkan ke KLON) + salin ke barang baru + daftar aksi di SALINAN; handler ASLI pohon POHON; tulis hanya di klon; klon DIHAPUS (trap).
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-kategori.sh (cek-sibuk dulu). rc 0 = semua cek hijau.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
python3 "$D/cek-saring-tenant.py" "$0" "$D/uji-salinan-kategori.py" || { echo "rc=3 (SQL tanpa saring tenant)"; exit 3; }
P=milkyhoop-dev-postgres-1; TS=$(date +%s); POHON=${POHON:-/root/mh-kat}; S=scratch_kat_$TS; O=$(mktemp -d)
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf "$O"
          echo "salinan dihapus; sisa DB scratch_kat%: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_kat%'")"; }
trap hapus EXIT
docker exec $P sh -c "pg_dump -U postgres -d milkydb --no-owner --no-acl -Fc -f /tmp/kat_$TS.dump" || exit 2
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P pg_restore -U postgres -d $S --no-owner --no-acl /tmp/kat_$TS.dump >/tmp/kat_restore.log 2>&1
docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 <"$POHON/backend/migrations/V505__item_categories_akun.sql" >/dev/null || { echo "rc=2 (V505 gagal di klon)"; exit 2; }
echo "restore galat: $(grep -c ERROR /tmp/kat_restore.log)"; docker exec $P rm -f /tmp/kat_$TS.dump
timeout 900 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -e DB_NAME=$S -e KELUAR=/o/kat.json \
  -e TENANT=${TENANT:-kaos-biru-konveksi,pt-kita-nyawit-bersama} -v "$POHON/backend:/app/backend:ro" -v "$D/uji-salinan-kategori.py:/uji.py:ro" -v "$O:/o" \
  -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py 2>&1 | grep -E "^\[|Traceback|  File|Error|BUKAN"
echo "rc=${PIPESTATUS[0]} ($POHON @ $(git -C "$POHON" rev-parse --short HEAD))"
