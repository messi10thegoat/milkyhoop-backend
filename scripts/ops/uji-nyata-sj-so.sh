#!/bin/bash
# Harness nyata R1 irisan 1: scratch DB dari dump terbaru; foto SO SEBELUM V415; terapkan V415; foto SESUDAH (harus IDENTIK);
# lalu sj_harness_nyata.py dalam transaksi ROLLBACK.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_sj_$$
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/sj_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM sales_orders"); echo "SO di scratch: $n"
[ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch kosong"; exit 3; }
Q="SELECT id, shipped_qty, invoiced_qty, status, so_memenuhi_selesai(id) AS memenuhi FROM sales_orders ORDER BY id"
docker exec $PGC psql -U postgres -d $DB -At -c "$Q" > /tmp/sj_pre.txt
docker exec $PGC psql -U postgres -d $DB -At -c "SELECT tenant_id, (SELECT count(*) FROM verify_so_shipped_reconciliation(tenant_id)) FROM (SELECT DISTINCT tenant_id FROM sales_orders) t ORDER BY 1" >> /tmp/sj_pre.txt
echo "-- V415 pada skema nyata (-1, ON_ERROR_STOP)"
docker exec -i $PGC psql -U postgres -d $DB -v ON_ERROR_STOP=1 -1 -q < "$REPO/backend/migrations/V415__sj_so_tanpa_faktur.sql" || { echo "V415 GAGAL"; exit 4; }
docker exec $PGC psql -U postgres -d $DB -At -c "$Q" > /tmp/sj_post.txt
docker exec $PGC psql -U postgres -d $DB -At -c "SELECT tenant_id, (SELECT count(*) FROM verify_so_shipped_reconciliation(tenant_id)) FROM (SELECT DISTINCT tenant_id FROM sales_orders) t ORDER BY 1" >> /tmp/sj_post.txt
echo "baris dibandingkan: $(wc -l < /tmp/sj_pre.txt)"
if diff -q /tmp/sj_pre.txt /tmp/sj_post.txt >/dev/null; then echo "IDENTIK: $n SO (shipped/invoiced/status/memenuhi + rekonsiliasi) sebelum == sesudah V415"; else echo "BEDA sesudah V415:"; diff /tmp/sj_pre.txt /tmp/sj_post.txt | head; exit 5; fi
docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM (SELECT recompute_so_shipped(id) FROM sales_orders) x" >/dev/null
docker exec $PGC psql -U postgres -d $DB -At -c "$Q" > /tmp/sj_re.txt
if diff -q /tmp/sj_pre.txt <(head -n $(wc -l < /tmp/sj_re.txt) /tmp/sj_re.txt) >/dev/null 2>&1 || diff -q <(head -n $n /tmp/sj_pre.txt) /tmp/sj_re.txt >/dev/null; then echo "IDENTIK sesudah recompute_so_shipped() atas SEMUA SO"; else echo "BEDA sesudah recompute:"; diff <(head -n $n /tmp/sj_pre.txt) /tmp/sj_re.txt | head; exit 6; fi
URLASLI=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL)
URL=$(echo "$URLASLI" | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#")
docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e PYTHONPATH=/repo:/repo/backend/api_gateway/app:/repo/backend/api_gateway/libs:/repo/backend/services \
  -v $REPO:/repo:ro -v $REPO/backend/api_gateway/tests/sj_harness_nyata.py:/h/h.py:ro milkyhoop-dev-api_gateway:latest python /h/h.py
echo "rc=$?"
