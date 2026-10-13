#!/bin/bash
# Uji nyata W-20261013-SJSO-SUDAHFAKTUR: scratch DB dari backup terbaru + V415/V417 (bila perlu); handler asli; tx ROLLBACK;
# kontrol merah = kode master (services/sj_so.py + routers/sj_so.py ditimpa dari deploy/master) -> HARUS merah.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_sjdf_$$
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1; rm -f /tmp/sjdf_m_s.py /tmp/sjdf_m_r.py' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/sjdf_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM sales_orders"); [ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch kosong"; exit 3; }
for m in V415__sj_so_tanpa_faktur V417__barang_terkirim_koa_peran_sumber; do
  if [ "$m" = "V415__sj_so_tanpa_faktur" ] && [ "$(docker exec $PGC psql -U postgres -d $DB -Atc "select count(*) from information_schema.columns where table_name='invoice_fulfillments' and column_name='asal'")" != "0" ]; then continue; fi
  docker exec -i $PGC psql -U postgres -d $DB -v ON_ERROR_STOP=1 -1 -q < "$REPO/backend/migrations/$m.sql" >/dev/null 2>&1 || { echo "$m GAGAL"; exit 4; }
done
URL=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#")
APP=/repo/backend/api_gateway/app
ENVS=(-e URL="$URL" -e MON=/repo/monitoring/accounting_health_check.sh -e PYTHONPATH=/repo:/repo/backend/api_gateway/app:/repo/backend/api_gateway/libs:/repo/backend/services)
H=(-v $REPO/backend/api_gateway/tests/sj_difaktur_harness_nyata.py:/h/h.py:ro milkyhoop-dev-api_gateway:latest python /h/h.py)
echo "=== KODE BARU"
docker run --rm --network milkyhoop_dev_network "${ENVS[@]}" -v $REPO:/repo:ro "${H[@]}" > /tmp/sjdf_baru.log 2>&1; cat /tmp/sjdf_baru.log | grep -vE "^\s*$" | tail -40
git -C "$REPO" show deploy/master:backend/api_gateway/app/services/sj_so.py > /tmp/sjdf_m_s.py
git -C "$REPO" show deploy/master:backend/api_gateway/app/routers/sj_so.py > /tmp/sjdf_m_r.py
echo "=== KONTROL MERAH (kode master; HARUS merah)"
docker run --rm --network milkyhoop_dev_network "${ENVS[@]}" -v $REPO:/repo:ro -v /tmp/sjdf_m_s.py:$APP/services/sj_so.py:ro -v /tmp/sjdf_m_r.py:$APP/routers/sj_so.py:ro "${H[@]}" > /tmp/sjdf_master.log 2>&1
grep -E "GAGAL|OK  |RINGKAS|Traceback|Error" /tmp/sjdf_master.log | head -30
baru=$(grep -c "^  GAGAL " /tmp/sjdf_baru.log); lama=$(grep -c "^  GAGAL " /tmp/sjdf_master.log)
echo "GAGAL baru=$baru master=$lama"
[ "$baru" = 0 ] && [ "$lama" -gt 0 ] && grep -q "^RINGKAS" /tmp/sjdf_baru.log && echo "UJI NYATA HIJAU (kontrol merah terbukti)" || { echo "UJI NYATA TIDAK HIJAU"; exit 1; }
