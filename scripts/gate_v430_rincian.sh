#!/usr/bin/env bash
# Gerbang V430 compute_ar_rincian (S1-U3, 20 Okt 2026).
#  1. BADAN fungsi (teks migrasi) vs compute_ar_outstanding di milkydb, SEMUA faktur SEMUA tenant, READ ONLY (ROLLBACK).
#  2. DB scratch milkydb_v430 = SKEMA milkydb saja (nol baris data): terapkan V430, md5 compute_ar_outstanding TAK berubah,
#     fungsi terpanggil, ROLLBACK -> fungsi hilang, terapkan ulang. DB scratch dihapus.
# Kontrol merah: --kontrol-merah dp | proporsional  -> badan disabotase di salinan sementara -> HARUS GAGAL.
set -uo pipefail
P=milkyhoop-dev-postgres-1; DB=milkydb_v430; G=milkyhoop-dev-api_gateway
AKAR="$(cd "$(dirname "$0")/.." && pwd)"; MIG="$AKAR/backend/migrations/V430__compute_ar_rincian.sql"
RB="$AKAR/backend/migrations/V430__compute_ar_rincian_ROLLBACK.sql"
q() { docker exec -i "$P" psql -U postgres "$@"; }
SRC=$(mktemp /root/.v430-mig-XXXX.sql); cp "$MIG" "$SRC"
case "${1:-}" in
  --kontrol-merah)
    case "${2:-}" in
      dp) sed -i 's|COALESCE(dp.total_credit, 0) AS dp,|0 AS dp,|' "$SRC"; grep -c "0 AS dp," "$SRC" ;;
      proporsional) sed -i 's|THEN ROUND(pb.ar_credit \* rpa.amount_applied|THEN ROUND(pb.ar_credit * 1|' "$SRC"; grep -c "pb.ar_credit \* 1" "$SRC" ;;
      *) echo "kontrol-merah: dp|proporsional"; exit 2 ;;
    esac ;;
esac
E=$(mktemp /root/.v430-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$AKAR/scripts/ops/uji-v430-rincian.py:/h.py:ro" \
  -v "$SRC:/mig.sql:ro" --entrypoint python "$IMG" -I /h.py badan /mig.sql > /tmp/v430_badan.log 2>&1
rm -f "$E"
grep -E "^(PASS|FAIL)|faktur ber-sumber|RINGKAS|Traceback|Error" /tmp/v430_badan.log
rc=0; grep -q "RINGKAS gagal=0" /tmp/v430_badan.log || rc=1
if [ -z "${1:-}" ]; then
  q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
  docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
  md5() { q -d "$DB" -At -c "SELECT md5(pg_get_functiondef('public.compute_ar_outstanding(text)'::regprocedure))"; }
  m0=$(md5)
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$MIG" || { echo "FAIL terapkan"; rc=1; }
  [ "$(md5)" = "$m0" ] && echo "PASS compute_ar_outstanding tak berubah (md5 $m0)" || { echo "FAIL compute_ar_outstanding berubah"; rc=1; }
  [ "$(q -d "$DB" -At -c "SELECT count(*) FROM compute_ar_rincian('t')")" = 0 ] && echo "PASS fungsi terpanggil (skema scratch tanpa data)" || { echo "FAIL fungsi"; rc=1; }
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$RB" || { echo "FAIL rollback"; rc=1; }
  [ "$(q -d "$DB" -At -c "SELECT to_regprocedure('public.compute_ar_rincian(text)') IS NULL")" = t ] && echo "PASS rollback: fungsi hilang" || { echo "FAIL rollback"; rc=1; }
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$MIG" && echo "PASS terapkan ulang sesudah rollback" || { echo "FAIL terapkan ulang"; rc=1; }
  q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
fi
rm -f "$SRC" /tmp/v430_badan.log
[ $rc = 0 ] && { echo LULUS; exit 0; }; echo GAGAL; exit 1
