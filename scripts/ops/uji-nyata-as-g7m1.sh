#!/usr/bin/env bash
# AS-G7-M1: pratinjau massal hapus SO/penawaran + batal proforma baca saja. kaos, NOL commit. MERAH=1: SALINAN pohon BARU tersabotase (status pratinjau batal proforma "canceled") -> (P-proforma) WAJIB merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.g7m1-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.g7m1-out-XXXX); S=$(mktemp -d /root/.g7m1-sab-XXXX)
trap 'mkdir -p /root/logs && cp -a "$O" "/root/logs/g7m1-out-$(date -u +%Y%m%dT%H%M%S)" 2>/dev/null; rm -rf "$E" "$O" "$S"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e PEMBANDING="${PEMBANDING:-baru}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-as-g7m1.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
jalan "$UTAMA" PRATINJAU lama || rc=1
jalan "$AKAR" PRATINJAU baru || rc=1
if [ "${MERAH:-0}" = 1 ]; then
  cp -a "$AKAR/backend" "$S/backend"
  F="$S/backend/api_gateway/app/routers/bulk_proforma.py"
  [ "$(grep -c 'return \[\], {"proforma_number": nomor, "status": "cancelled"}' "$F")" = 1 ] || { echo "sabotase: titik tak unik"; exit 2; }
  sed -i 's/return \[\], {"proforma_number": nomor, "status": "cancelled"}/return [], {"proforma_number": nomor, "status": "canceled"}/' "$F"
  echo "SABOTASE status pratinjau batal proforma ($(grep -c '"status": "canceled"' "$F"))"
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
