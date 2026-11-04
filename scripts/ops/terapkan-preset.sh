#!/usr/bin/env bash
# Preset flag kategori usaha (lihat terapkan-preset.py). Kode = pohon tempat skrip ini berada; image = gateway prod.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.preset-env-XXXX); chmod 600 "$E"; trap 'rm -f "$E"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MERAH="${MERAH:-}" -v "$AKAR/backend:/app/backend:ro" \
    -v "$AKAR/scripts/ops/terapkan-preset.py:/p.py:ro" -w /app/backend/api_gateway --entrypoint python \
    "$(docker inspect "$G" --format '{{.Config.Image}}')" -I /p.py "$@" 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
exit "${PIPESTATUS[0]}"
