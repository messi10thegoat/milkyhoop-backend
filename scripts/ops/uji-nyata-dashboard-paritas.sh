#!/usr/bin/env bash
# Pelari paritas dashboard v2 == /all == journal_lines (pohon ini). Baca-saja (transaksi READ ONLY); DSN lewat berkas env 600.
# MERAH=1 = kontrol merah (harus gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.dsp-env-XXXX); chmod 600 "$E"; trap 'rm -f "$E"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MERAH="${MERAH:-0}" -v "$AKAR/backend:/app/backend:ro" \
  -v "$AKAR/scripts/ops/uji-nyata-dashboard-paritas.py:/h.py:ro" -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 \
  | grep -v -E "^(INFO|DEBUG|WARNING)"
exit "${PIPESTATUS[0]}"
