#!/usr/bin/env bash
# Pelari Faktur Pembelian dari compute_ap_rincian: lama (pohon utama) -> baru (pohon ini) -> banding. Baca-saja; DSN lewat berkas env 600.
# MERAH=1 = kontrol merah (harus gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; LAMA=${MH_MAIN:-/root/milkyhoop-dev}; G=milkyhoop-dev-api_gateway
O=$(mktemp -d /root/.tgr-XXXX); E=$(mktemp /root/.tgr-env-XXXX); chmod 600 "$E"; trap 'rm -rf "$O" "$E"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
j() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MERAH="${MERAH:-0}" -v "$1/backend:/app/backend:ro" -v "$O:/out" \
      -v "$AKAR/scripts/ops/uji-nyata-tagihan-rincian.py:/h.py:ro" -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py "${@:2}" 2>&1 \
      | grep -v -E "^(INFO|DEBUG|WARNING)"; }
[ "${MERAH:-0}" = 1 ] || j "$LAMA" lama /out/lama.json | tail -2
j "$AKAR" baru /out/baru.json
[ "${MERAH:-0}" = 1 ] || j "$AKAR" banding /out/lama.json /out/baru.json
