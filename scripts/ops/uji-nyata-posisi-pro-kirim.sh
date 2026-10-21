#!/usr/bin/env bash
# Pelari position_text Proforma+Pengiriman (baca-saja; DSN lewat env dari docker exec, tak dicetak). MERAH=1 = kontrol merah (harus gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network -e URL="$(docker exec "$G" printenv DATABASE_URL)" -e MERAH="${MERAH:-0}" \
  -v "$AKAR/backend/api_gateway:/app/backend/api_gateway:ro" -v "$AKAR/scripts/ops/uji-nyata-posisi-pro-kirim.py:/h.py:ro" \
  -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)" | tail -12
