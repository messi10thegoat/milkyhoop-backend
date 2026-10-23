#!/usr/bin/env bash
# Pelari paritas /bills/summary LAMA (pohon utama /root/milkyhoop-dev = kode live) vs BARU (pohon ini). Baca-saja; DSN lewat berkas env 600.
# MERAH=1 = kontrol merah (harus gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; LAMA=${LAMA:-/root/milkyhoop-dev}; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.tsl-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.tsl-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}'); H="$AKAR/scripts/ops/uji-nyata-tagihan-summary-lama-baru.py"
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MERAH="${MERAH:-0}" "$@" -v "$H:/h.py:ro" -v "$O:/o" \
  -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
jalan -v "$LAMA/backend:/app/backend:ro" -e TULIS=/o/lama.json || exit 2
jalan -v "$AKAR/backend:/app/backend:ro" -e TULIS=/o/baru.json || exit 2
jalan -v "$AKAR/backend:/app/backend:ro" -e BANDING=/o/lama.json,/o/baru.json
