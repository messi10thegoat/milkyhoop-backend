#!/bin/bash
# Harness nyata R2: scratch DB dari dump terbaru, V412 diterapkan, lalu pb_h.py dalam transaksi ROLLBACK.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"   # worktree/main tree yang berisi kode + V412
PGC=milkyhoop-dev-postgres-1; DB=scratch_pb_$$
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/pb_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM inventory_ledger"); echo "ledger di scratch: $n"
[ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch kosong"; exit 3; }
echo "-- V412 pada skema nyata (-1, ON_ERROR_STOP)"
docker exec -i $PGC psql -U postgres -d $DB -v ON_ERROR_STOP=1 -1 < $REPO/backend/migrations/V412__pemindahan_barang.sql || { echo "V412 GAGAL"; exit 4; }
echo "-- fungsi lama hilang?"; docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM pg_proc WHERE proname IN ('ship_stock_transfer','receive_stock_transfer')"
URLASLI=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL)
URL=$(echo "$URLASLI" | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#")
docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e PYTHONPATH=/repo:/repo/backend/api_gateway/app:/repo/backend/api_gateway/libs:/repo/backend/services \
  -v $REPO:/repo:ro -v $REPO/backend/api_gateway/tests/pb_harness_nyata.py:/h/pb_h.py:ro milkyhoop-dev-api_gateway:latest python /h/pb_h.py
echo "rc=$?"
