#!/bin/bash
# Harness ALUR PO pratinjau -> tulis (kontrak PO tahap 2, 22 Okt 2026): DB scratch dari dump terbaru (dibuang sesudahnya), kode router
# pohon ini. Berat (restore dump di kontainer postgres) -> JANGAN saat window/restart/apply_mig sesi lain (slot suite).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_poalur_$$
for n in $(pgrep -fc "^bash /root/window_item.sh") $(pgrep -fc "^(bash )?[^ ]*mh-gerbang") $(pgrep -fc "^bash /root/apply_mig.sh"); do
  [ "$n" = 0 ] || { echo "TOLAK: window/gerbang/apply_mig sedang berjalan"; exit 9; }
done
POHON="$REPO"; SAB=""
if [ "${1:-}" = "--kontrol-merah" ]; then   # sabotase: angka /calculate +0.01 -> "create == calculate" HARUS gagal
  SAB=$(mktemp -d /root/.poalur-sab-XXXX); cp -a "$REPO/backend" "$SAB/backend"; POHON="$SAB"
  f="$SAB/backend/api_gateway/app/services/po_rencana.py"
  grep -q '"total_amount": _f(t\["total_amount"\]),' "$f" || { echo "pola sabotase tak ditemukan"; exit 8; }
  sed -i 's/"total_amount": _f(t\["total_amount"\]),/"total_amount": _f(t["total_amount"] + Decimal("0.01")),/' "$f"
fi
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1; [ -n "$SAB" ] && rm -rf "$SAB"' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/poalur_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM vendors WHERE tenant_id = 'kaos-biru-konveksi'"); echo "vendor kaos di scratch: $n"
[ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch tanpa vendor kaos"; exit 3; }
URLASLI=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL)
URL=$(echo "$URLASLI" | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#"); unset URLASLI
docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e OPENAI_API_KEY=boneka \
  -v "$POHON":/repo:ro -v "$REPO/scripts/ops/uji-nyata-po-alur.py":/h/h.py:ro --entrypoint python milkyhoop-dev-api_gateway:latest -I /h/h.py
rc=$?; echo "rc=$rc"
if [ -n "$SAB" ]; then
  [ "$rc" -ne 0 ] && { echo "KONTROL MERAH: terdeteksi -> OK"; exit 0; }
  echo "KONTROL MERAH GAGAL: sabotase tidak terdeteksi"; exit 1
fi
exit $rc
