#!/usr/bin/env bash
# Item 7: int() uang -> Decimal HALF_UP. kaos, NOL commit. LAMA (pohon utama) = kontrol memotong; BARU = tanpa potong.
# MERAH=1: SALINAN pohon BARU dengan _uang() memotong (int) -> (H2 outstanding-summary) WAJIB merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.p7-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.p7-out-XXXX); S=$(mktemp -d /root/.p7-sab-XXXX)
trap 'mkdir -p /root/logs && cp -a "$O" "/root/logs/p7-out-$(date -u +%Y%m%dT%H%M%S)" 2>/dev/null; rm -rf "$E" "$O" "$S"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e PEMBANDING="${PEMBANDING:-baru}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-p2p7.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
jalan "$UTAMA" REKAM lama || rc=1
if [ "${MERAH:-0}" = 1 ]; then
  cp -a "$AKAR/backend" "$S/backend"
  F="$S/backend/api_gateway/app/services/bills_service.py"
  [ "$(grep -c 'return int(d) if d == d.to_integral_value() else float(d)' "$F")" = 1 ] || { echo "sabotase: titik tak unik"; exit 2; }
  sed -i 's/return int(d) if d == d.to_integral_value() else float(d)/return int(v or 0)/' "$F"; echo "SABOTASE bills_service._uang = int ($(grep -c 'return int(v or 0)' "$F"))"
  jalan "$S" REKAM merah || rc=1
  jalan "$AKAR" JEPRET sesudah || rc=1
  out=$(PEMBANDING=merah jalan "$AKAR" BANDING x); r=$?; echo "$out" | grep -E "^FAIL|RINGKAS"
  echo "$out" | grep -q "^FAIL (H2) BARU outstanding-summary" && m=1 || m=0
  echo "rc_banding_merah=$r (harus 1) H2_merah=$m (harus 1)"; [ $r = 1 ] && [ $m = 1 ] || rc=1
  echo "rc=$rc"; exit $rc
fi
jalan "$AKAR" REKAM baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
