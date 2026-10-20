#!/usr/bin/env bash
# Pelari harness S1-U2: bundel SO lama (pohon utama) vs baru (pohon ini) + bundel faktur. BACA saja (ROLLBACK).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; LAMA=${MH_MAIN:-/root/milkyhoop-dev}; G=milkyhoop-dev-api_gateway
O=$(mktemp -d /root/.dokfak-XXXX); E=$(mktemp /root/.dokfak-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
j() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$1/backend:/app/backend:ro" -v "$O:/out" \
      -v "$AKAR/scripts/ops/uji-nyata-dokumen-faktur.py:/h.py:ro" --entrypoint python "$IMG" -I /h.py "${@:2}"; }
f() { grep -E "PASS|FAIL|RINGKAS|dump|lepas|Traceback|Error"; }
j "$LAMA" so /out/lama.json 2>&1 | f
j "$AKAR" so /out/baru.json 2>&1 | f
j "$AKAR" banding /out/lama.json /out/baru.json 2>&1 | f
j "$AKAR" faktur 2>&1 | f
rm -rf "$O" "$E"
