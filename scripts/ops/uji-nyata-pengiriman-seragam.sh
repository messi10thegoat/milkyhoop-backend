#!/usr/bin/env bash
# Pelari: lama (pohon utama master) -> baru (pohon ini) -> banding. Baca-saja; DSN lewat env dari docker exec (tak dicetak).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; LAMA=${MH_MAIN:-/root/milkyhoop-dev}; G=milkyhoop-dev-api_gateway
O=$(mktemp -d /root/.png-XXXX); IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
j() { docker run --rm --network milkyhoop_dev_network -e URL="$(docker exec "$G" printenv DATABASE_URL)" -v "$1/backend/api_gateway:/app/backend/api_gateway:ro" -v "$O:/out" \
      -v "$AKAR/scripts/ops/uji-nyata-pengiriman-seragam.py:/h.py:ro" -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py "${@:2}"; }
j "$LAMA" lama /out/lama.json 2>&1 | tail -3
j "$AKAR" baru /out/baru.json 2>&1 | tail -6
j "$AKAR" banding /out/lama.json /out/baru.json 2>&1 | tail -3
rm -rf "$O"
