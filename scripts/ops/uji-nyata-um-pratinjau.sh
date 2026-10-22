#!/bin/bash
# Harness ALUR pratinjau->tulis UM Pembelian (CW penuh, 22 Okt 2026): scratch DB dari dump terbaru, lalu uji-nyata-um-pratinjau.py (kode router ASLI)
# dalam transaksi ROLLBACK. Berat (restore dump di kontainer postgres) -> JANGAN saat window/restart/apply_mig sesi lain (slot suite).
#   bash scripts/ops/uji-nyata-um-pratinjau.sh                  # kode pohon ini
#   bash scripts/ops/uji-nyata-um-pratinjau.sh --kontrol-merah  # salinan pohon disabotase (lepas tanpa reversed_by_id) -> HARUS gagal
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_ump_$$
for n in $(pgrep -fc "^bash /root/window_item.sh") $(pgrep -fc "^(bash )?[^ ]*mh-gerbang") $(pgrep -fc "^bash /root/apply_mig.sh"); do
  [ "$n" = 0 ] || { echo "TOLAK: window/gerbang/apply_mig sedang berjalan"; exit 9; }
done
POHON="$REPO"; SAB=""
if [ "${1:-}" = "--kontrol-merah" ]; then
  SAB=$(mktemp -d /root/.ump-sab-XXXX); cp -a "$REPO/backend" "$SAB/backend"; POHON="$SAB"
  python3 - "$SAB/backend/api_gateway/app/routers/vendor_deposits.py" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
a = '"UPDATE journal_entries SET reversed_by_id = $1 WHERE id = $2 AND reversed_by_id IS NULL", rev_id, asli["id"])'
assert s.count(a) == 1, "pola sabotase tak ditemukan"
s = s.replace(a, '"SELECT $1::uuid, $2::uuid", rev_id, asli["id"])').replace('if ditandai != "UPDATE 1":', 'if False:')
open(p, "w").write(s)
PY
fi
DUMP=$(ls -t /root/backups/milkydb-pre-*.dump | head -1)
echo "dump: $DUMP db: $DB pohon: $POHON"
docker exec $PGC psql -U postgres -d postgres -qc "CREATE DATABASE $DB" || exit 2
trap 'docker exec $PGC psql -U postgres -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1; [ -n "$SAB" ] && rm -rf "$SAB"' EXIT
docker exec -i $PGC pg_restore -U postgres -d $DB --no-owner < "$DUMP" > /tmp/umt_restore.log 2>&1; echo "restore rc=$? (peringatan diizinkan)"
n=$(docker exec $PGC psql -U postgres -d $DB -Atc "SELECT count(*) FROM journal_entries"); echo "jurnal di scratch: $n"
[ "${n:-0}" -gt 0 ] || { echo "HARNESS RUSAK: scratch kosong"; exit 3; }
URLASLI=$(docker exec milkyhoop-dev-api_gateway printenv DATABASE_URL)
URL=$(echo "$URLASLI" | sed -E "s#/[^/?]+(\?.*)?\$#/$DB\1#"); unset URLASLI
docker run --rm --network milkyhoop_dev_network -e URL="$URL" -e OPENAI_API_KEY=boneka -e PYTHONPATH=/repo/backend/api_gateway \
  -v "$POHON":/repo:ro -v "$REPO/scripts/ops/uji-nyata-um-pratinjau.py":/h/h.py:ro --entrypoint python milkyhoop-dev-api_gateway:latest -I /h/h.py
rc=$?; echo "rc=$rc"
if [ -n "$SAB" ]; then
  [ "$rc" -ne 0 ] && { echo "KONTROL MERAH: terdeteksi (harness gagal pada sabotase) -> OK"; exit 0; }
  echo "KONTROL MERAH GAGAL: sabotase tidak terdeteksi"; exit 1
fi
exit $rc
