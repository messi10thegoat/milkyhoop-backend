#!/usr/bin/env bash
# AS-G7c: pratinjau aksi uang muka + pindah proforma baca saja. kaos, NOL commit. MERAH=1: SALINAN pohon BARU tersabotase
# (sesudah apply: sisa dipotong 2x) -> (P-apply) WAJIB merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.g7c-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.g7c-out-XXXX); S=$(mktemp -d /root/.g7c-sab-XXXX)
trap 'mkdir -p /root/logs && cp -a "$O" "/root/logs/g7c-out-$(date -u +%Y%m%dT%H%M%S)" 2>/dev/null; rm -rf "$E" "$O" "$S"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e PEMBANDING="${PEMBANDING:-baru}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-as-g7c.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
if [ "${HANYA_TULIS:-0}" = 1 ]; then  # diagnosis (T): pohon LAMA dua kali (determinisme) + BARU, laporan jalur medan beda
  jalan "$UTAMA" PRATINJAU lama >/dev/null || rc=1   # menulis kasus.json (baca + pratinjau lama ber-ROLLBACK)
  jalan "$UTAMA" TULIS lama || rc=1
  jalan "$UTAMA" TULIS lama2 || rc=1
  jalan "$AKAR" TULIS baru || rc=1
  cp -a "$AKAR/backend" "$S/backend"; F="$S/backend/api_gateway/app/services/uang_muka_rencana.py"
  [ "$(grep -c 'f"Bayar Refund - {' "$F")" = 1 ] || { echo "sabotase T: titik tak unik"; exit 2; }
  sed -i 's/f"Bayar Refund - {/f"Bayar Refund! - {/' "$F"; echo "SABOTASE T memo refund ($(grep -c 'Bayar Refund! - ' "$F"))"
  jalan "$S" TULIS merah || rc=1
  jalan "$AKAR" JEPRET sesudah || rc=1
  jalan "$AKAR" LAPOR x || rc=1
  echo "rc=$rc"; exit $rc
fi
jalan "$UTAMA" PRATINJAU lama || rc=1
jalan "$AKAR" PRATINJAU baru || rc=1
if [ "${MERAH:-0}" = 1 ]; then
  cp -a "$AKAR/backend" "$S/backend"
  F="$S/backend/api_gateway/app/services/uang_muka_rencana.py"
  A='            dipakai, sisa = dipakai + p["total"], sisa - p["total"]'
  [ "$(grep -cxF "$A" "$F")" = 1 ] || { echo "sabotase: titik tak unik"; exit 2; }
  sed -i 's/^            dipakai, sisa = dipakai + p\["total"\], sisa - p\["total"\]$/            dipakai, sisa = dipakai + p["total"], sisa - p["total"] * 2/' "$F"
  echo "SABOTASE sesudah apply: sisa dipotong 2x ($(grep -c 'p\["total"\] \* 2' "$F"))"
  jalan "$S" PRATINJAU merah || rc=1
  jalan "$AKAR" JEPRET sesudah || rc=1
  PEMBANDING=merah jalan "$AKAR" BANDING x; r=$?; echo "rc_banding_merah=$r (harus 1)"; [ $r = 1 ] || rc=1
  echo "rc=$rc"; exit $rc
fi
jalan "$UTAMA" TULIS lama || rc=1
jalan "$AKAR" TULIS baru || rc=1
jalan "$UTAMA" KUNCI lama || rc=1
jalan "$AKAR" KUNCI baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
