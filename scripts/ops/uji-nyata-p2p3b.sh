#!/usr/bin/env bash
# P2P-3b: pratinjau buat ND == create + posting tertulis. kaos, NOL commit. MERAH=1: SALINAN pohon BARU tersabotase (perkiraan nomor ND +2 dan
# baris maya diabaikan) -> (NOMOR) + (STOK) WAJIB merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.p3b-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.p3b-out-XXXX); S=$(mktemp -d /root/.p3b-sab-XXXX)
trap 'mkdir -p /root/logs && cp -a "$O" "/root/logs/p3b-out-$(date -u +%Y%m%dT%H%M%S)" 2>/dev/null; rm -rf "$E" "$O" "$S"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e PEMBANDING="${PEMBANDING:-baru}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-p2p3b.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
if [ "${MERAH:-0}" = 1 ]; then
  cp -a "$AKAR/backend" "$S/backend"
  F="$S/backend/api_gateway/app/services/nota_debit_rencana.py"
  [ "$(grep -c "'YYYY-MM')), 0) + 1)::text," "$F")" = 1 ] || { echo "sabotase: titik nomor tak unik"; exit 2; }
  sed -i "s/'YYYY-MM')), 0) + 1)::text,/'YYYY-MM')), 0) + 2)::text,/" "$F"
  [ "$(grep -c "hpp, stok = await rencana_hpp(conn, tid, vc, items)" "$F")" = 1 ] || { echo "sabotase: titik hpp tak unik"; exit 2; }
  sed -i "s/hpp, stok = await rencana_hpp(conn, tid, vc, items)/hpp, stok = await rencana_hpp(conn, tid, vc)/" "$F"
  echo "SABOTASE nomor +2 ($(grep -c "0) + 2)::text," "$F")) + baris maya diabaikan ($(grep -c "rencana_hpp(conn, tid, vc)$" "$F"))"
  jalan "$S" REKAM merah || rc=1
  jalan "$AKAR" JEPRET sesudah || rc=1
  out=$(PEMBANDING=merah jalan "$AKAR" BANDING x); r=$?; echo "$out" | grep -E "^FAIL|RINGKAS"
  echo "$out" | grep -q "^FAIL .*(NOMOR)" && n=1 || n=0; echo "$out" | grep -q "^FAIL .*(STOK)" && s=1 || s=0
  echo "rc_banding_merah=$r (harus 1) NOMOR_merah=$n STOK_merah=$s (harus 1/1)"; [ $r = 1 ] && [ $n = 1 ] && [ $s = 1 ] || rc=1
  echo "rc=$rc"; exit $rc
fi
jalan "$AKAR" REKAM baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
