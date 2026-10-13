#!/bin/bash
# Uji nyata W-20261013-VENDORFIELD: scratch DB dari backup terbaru + V420 (IF NOT EXISTS, idempoten), handler ASLI,
# satu transaksi ROLLBACK, kontrol merah = kode master (vendors.py + schemas/vendors.py ditimpa dari deploy/master).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_vf_$$
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1; rm -f /tmp/vf_master_r.py /tmp/vf_master_s.py' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/vf_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM vendors WHERE tenant_id='kaos-biru-konveksi'"); echo "pemasok kaos di scratch: $n"
[ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch kosong"; exit 3; }
docker exec -i $PGC psql -U postgres -d $DB -v ON_ERROR_STOP=1 -1 -q < "$REPO/backend/migrations/V420__identitas_pajak_pelanggan_pemasok.sql" || { echo "V420 GAGAL"; exit 4; }
URL=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#")
APP=/repo/backend/api_gateway/app
run() { docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e PYTHONPATH=/repo/backend/api_gateway:/repo/backend/api_gateway/libs:/repo/backend/services \
  "$@" -v $REPO:/repo:ro -v $REPO/backend/api_gateway/tests/vendor_field_harness_nyata.py:/h/h.py:ro milkyhoop-dev-api_gateway:latest python /h/h.py; }
echo "=== KODE BARU"
run > /tmp/vf_baru.log 2>&1; cat /tmp/vf_baru.log
git -C "$REPO" show deploy/master:backend/api_gateway/app/routers/vendors.py > /tmp/vf_master_r.py
git -C "$REPO" show deploy/master:backend/api_gateway/app/schemas/vendors.py > /tmp/vf_master_s.py
echo "=== KONTROL MERAH (kode master; HARUS merah)"
docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e PYTHONPATH=/repo/backend/api_gateway:/repo/backend/api_gateway/libs:/repo/backend/services \
  -v $REPO:/repo:ro -v /tmp/vf_master_r.py:$APP/routers/vendors.py:ro -v /tmp/vf_master_s.py:$APP/schemas/vendors.py:ro \
  -v $REPO/backend/api_gateway/tests/vendor_field_harness_nyata.py:/h/h.py:ro milkyhoop-dev-api_gateway:latest python /h/h.py > /tmp/vf_master.log 2>&1; grep -E "^(PASS|FAIL|RINGKAS)" /tmp/vf_master.log | head -40
baru=$(grep -c "^FAIL" /tmp/vf_baru.log); lama=$(grep -c "^FAIL" /tmp/vf_master.log)
echo "FAIL baru=$baru master=$lama"
[ "$baru" = 0 ] && [ "$lama" -gt 0 ] && grep -q "^RINGKAS" /tmp/vf_baru.log && echo "UJI NYATA HIJAU (kontrol merah terbukti)" || { echo "UJI NYATA TIDAK HIJAU"; exit 1; }
