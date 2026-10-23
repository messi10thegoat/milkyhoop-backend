#!/usr/bin/env bash
# V444 sesudah diterapkan: celah peran 0 (BACA) + penyesuaian saldo bank kaos nyata di transaksi ROLLBACK + net 0. MERAH=1 = kontrol merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.v444-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.v444-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$1" -e NAMA="$2" -e MERAH="${MERAH:-0}" \
    -v "$AKAR/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-v444.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan JEPRET sebelum || rc=1
jalan TULIS x || rc=1
jalan JEPRET sesudah || rc=1
jalan BANDING x || rc=1
echo "rc=$rc"; exit $rc
