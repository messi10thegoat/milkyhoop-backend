#!/bin/bash
# Harness nyata R1 irisan 2: scratch DB dari dump terbaru; V415 + V417 diterapkan (-1, ON_ERROR_STOP); lalu sj_stok_harness_nyata.py
# (neraca Barang Terkirim, retur lewat alokasi, kontrol merah per source_type) dalam transaksi ROLLBACK.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_stok_$$
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/stok_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM sales_orders"); echo "SO di scratch: $n"
[ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch kosong"; exit 3; }
for m in V415__sj_so_tanpa_faktur V417__barang_terkirim_koa_peran_sumber; do
  f="$REPO/backend/migrations/$m.sql"
  # V415 sudah terpasang di prod terbaru; lewati bila kolom asal sudah ada
  if [ "$m" = "V415__sj_so_tanpa_faktur" ] && [ "$(docker exec $PGC psql -U postgres -d $DB -Atc "select count(*) from information_schema.columns where table_name='invoice_fulfillments' and column_name='asal'")" != "0" ]; then echo "-- $m sudah ada di dump"; continue; fi
  echo "-- $m (-1, ON_ERROR_STOP)"
  docker exec -i $PGC psql -U postgres -d $DB -v ON_ERROR_STOP=1 -1 -q < "$f" || { echo "$m GAGAL"; exit 4; }
done
echo "-- peran/akun per tenant sesudah V417:"
docker exec $PGC psql -U postgres -d $DB -Atc "select count(*) from account_roles where role_key='GOODS_DELIVERED_NOT_INVOICED'"
URLASLI=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL)
URL=$(echo "$URLASLI" | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#")
docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e MON=/repo/monitoring/accounting_health_check.sh \
  -e PYTHONPATH=/repo:/repo/backend/api_gateway/app:/repo/backend/api_gateway/libs:/repo/backend/services \
  -v $REPO:/repo:ro -v $REPO/backend/api_gateway/tests/sj_stok_harness_nyata.py:/h/h.py:ro milkyhoop-dev-api_gateway:latest python /h/h.py
echo "rc=$?"
