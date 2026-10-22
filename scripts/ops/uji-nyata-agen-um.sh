#!/bin/bash
# Harness agen chat create_vendor_deposit (22 Okt 2026): payload rakitan agen -> posted + jurnal == pratinjau: scratch DB dari dump terbaru, lalu uji-nyata-agen-um.py (kode router ASLI)
# dalam transaksi ROLLBACK. Berat (restore dump di kontainer postgres) -> JANGAN saat window/restart/apply_mig sesi lain (slot suite).
#   bash scripts/ops/uji-nyata-agen-um.sh                  # kode pohon ini
#   bash scripts/ops/uji-nyata-agen-um.sh --kontrol-merah  # salinan pohon disabotase (medan simpan_dan_posting agen dibuang) -> HARUS gagal
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PGC=milkyhoop-dev-postgres-1; DB=scratch_agenum_$$
for n in $(pgrep -fc "^bash /root/window_item.sh") $(pgrep -fc "^(bash )?[^ ]*mh-gerbang") $(pgrep -fc "^bash /root/apply_mig.sh"); do
  [ "$n" = 0 ] || { echo "TOLAK: window/gerbang/apply_mig sedang berjalan"; exit 9; }
done
POHON="$REPO"; SAB=""
if [ "${1:-}" = "--kontrol-merah" ]; then
  SAB=$(mktemp -d /root/.agenum-sab-XXXX); cp -a "$REPO/backend" "$SAB/backend"; POHON="$SAB"
  python3 - "$SAB/backend/api_gateway/app/services/unified_agent/direct_action_registry.py" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
i = s.index('            FieldSpec(name="simpan_dan_posting"'); j = s.index("editable=False),", i) + len("editable=False),") + 1
s = s[:i] + s[j:]  # agen tanpa medan posting -> DRAF -> harus MERAH
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
  -v "$POHON":/repo:ro -v "$REPO/scripts/ops/uji-nyata-agen-um.py":/h/h.py:ro --entrypoint python milkyhoop-dev-api_gateway:latest -I /h/h.py
rc=$?; echo "rc=$rc"
if [ -n "$SAB" ]; then
  [ "$rc" -ne 0 ] && { echo "KONTROL MERAH: terdeteksi (harness gagal pada sabotase) -> OK"; exit 0; }
  echo "KONTROL MERAH GAGAL: sabotase tidak terdeteksi"; exit 1
fi
exit $rc
