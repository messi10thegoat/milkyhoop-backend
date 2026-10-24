#!/usr/bin/env bash
# AS-G7a: pratinjau konfirmasi SO baca saja. kaos, NOL commit. MERAH=1: pohon BARU tersabotase (perkiraan melompati satu nomor)
# di SALINAN -> pembanding (P)/(M) WAJIB merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.g7a-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.g7a-out-XXXX); S=$(mktemp -d /root/.g7a-sab-XXXX)
trap 'rm -rf "$E" "$O" "$S"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e PEMBANDING="${PEMBANDING:-baru}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-as-g7a.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
jalan "$UTAMA" PRATINJAU lama || rc=1
jalan "$AKAR" PRATINJAU baru || rc=1
if [ "${MERAH:-0}" = 1 ]; then
  cp -a "$AKAR/backend" "$S/backend"
  F="$S/backend/api_gateway/app/services/kode_order.py"
  [ "$(grep -c '        seq += 1$' "$F")" = 1 ] || { echo "sabotase: titik tak unik"; exit 2; }
  sed -i 's/^        seq += 1$/        seq += 2/' "$F"; echo "SABOTASE perkiraan: seq += 2 ($(grep -c 'seq += 2' "$F"))"
  jalan "$S" PRATINJAU merah || rc=1
  jalan "$AKAR" JEPRET sesudah || rc=1
  PEMBANDING=merah jalan "$AKAR" BANDING x; r=$?; echo "rc_banding_merah=$r (harus 1)"; [ $r = 1 ] || rc=1
  echo "rc=$rc"; exit $rc
fi
jalan "$UTAMA" KUNCI lama || rc=1
jalan "$AKAR" KUNCI baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
