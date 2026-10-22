#!/usr/bin/env bash
# Gerbang V441 atribusi nota debit (PEMBELIAN-SERAGAM unit (3a), 22 Okt 2026).
#  1. BADAN fungsi (teks migrasi) sebagai KUERI di milkydb (tanpa DDL): semua tenant identik dgn compute_ap_outstanding terpasang,
#     rincian == AP, + 4 skenario nota debit sintetis di kaos dalam transaksi ROLLBACK (bebas / tertaut / refund / void).
#  2. DB scratch milkydb_v441 = SKEMA milkydb saja: terapkan V441 -> md5 compute_ap_outstanding BERUBAH; ROLLBACK -> md5 KEMBALI persis;
#     terapkan ulang. DB scratch dihapus.
# Kontrol merah: --kontrol-merah disjoint | refund -> badan disabotase di salinan sementara -> HARUS GAGAL.
set -uo pipefail
P=milkyhoop-dev-postgres-1; DB=milkydb_v441; G=milkyhoop-dev-api_gateway
AKAR="$(cd "$(dirname "$0")/.." && pwd)"; MIG="$AKAR/backend/migrations/V441__ap_atribusi_nota_debit.sql"
RB="$AKAR/backend/migrations/V441__ap_atribusi_nota_debit_ROLLBACK.sql"
q() { docker exec -i "$P" psql -U postgres "$@"; }
SRC=$(mktemp /root/.v441-mig-XXXX.sql); cp "$MIG" "$SRC"
case "${1:-}" in
  --kontrol-merah)
    case "${2:-}" in
      disjoint) sed -i '0,/          AND vc.original_bill_id IS NULL/{/          AND vc.original_bill_id IS NULL/d}' "$SRC"; grep -c "AND vc.original_bill_id IS NULL" "$SRC" ;;
      refund) sed -i "s|AND je.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND')|AND je.source_type IN ('VENDOR_CREDIT')|" "$SRC"; grep -c "VENDOR_CREDIT_REFUND')" "$SRC" ;;
      *) echo "kontrol-merah: disjoint|refund"; exit 2 ;;
    esac ;;
esac
E=$(mktemp /root/.v441-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$AKAR/scripts/ops/uji-v441-nota-debit.py:/h.py:ro" \
  -v "$SRC:/mig.sql:ro" --entrypoint python "$IMG" -I /h.py badan /mig.sql > /tmp/v441_badan.log 2>&1
rm -f "$E"
grep -E "^(PASS|FAIL)|RINGKAS|Traceback|Error" /tmp/v441_badan.log
rc=0; grep -q "RINGKAS gagal=0" /tmp/v441_badan.log || rc=1
if [ -z "${1:-}" ]; then
  q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
  docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
  md5() { q -d "$DB" -At -c "SELECT md5(pg_get_functiondef('public.compute_ap_outstanding(text)'::regprocedure)) || md5(pg_get_functiondef('public.compute_ap_rincian(text)'::regprocedure))"; }
  m0=$(md5)
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$MIG" || { echo "FAIL terapkan"; rc=1; }
  m1=$(md5)
  [ "$m1" != "$m0" ] && echo "PASS terapkan: kedua fungsi berganti" || { echo "FAIL terapkan tak mengubah fungsi"; rc=1; }
  [ "$(q -d "$DB" -At -c "SELECT count(*) FROM compute_ap_outstanding('t')")" = 0 ] && echo "PASS fungsi terpanggil (skema scratch tanpa data)" || { echo "FAIL fungsi"; rc=1; }
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$RB" || { echo "FAIL rollback"; rc=1; }
  [ "$(md5)" = "$m0" ] && echo "PASS rollback: definisi kembali PERSIS (md5)" || { echo "FAIL rollback tak persis"; rc=1; }
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$MIG" && [ "$(md5)" = "$m1" ] && echo "PASS terapkan ulang sesudah rollback" || { echo "FAIL terapkan ulang"; rc=1; }
  q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
fi
rm -f "$SRC" /tmp/v441_badan.log
[ $rc = 0 ] && { echo LULUS; exit 0; }; echo GAGAL; exit 1
