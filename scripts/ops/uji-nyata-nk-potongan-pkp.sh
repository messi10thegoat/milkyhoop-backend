#!/usr/bin/env bash
# Pelari NK potongan bruto PKP: kaos, transaksi ROLLBACK (menulis di dalam tx lalu dibatalkan). DSN lewat berkas env 600 (tak dicetak).
# MERAH=1 = kontrol merah (harus gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
E=$(mktemp /root/.nkpkp-env-XXXX); chmod 600 "$E"; trap 'rm -f "$E"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MERAH="${MERAH:-0}" \
  -v "$AKAR/backend:/app/backend:ro" -v "$AKAR/scripts/ops/uji-nyata-nk-potongan-pkp.py:/h.py:ro" \
  -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
