#!/usr/bin/env bash
# Pelari paritas S2: detail lama (pohon utama) vs baru (pohon ini) + paritas baris==detail. BACA saja.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; LAMA=${MH_MAIN:-/root/milkyhoop-dev}; G=milkyhoop-dev-api_gateway
O=$(mktemp -d /root/.pars2-XXXX); E=$(mktemp /root/.pars2-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
j() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$1/backend:/app/backend:ro" -v "$O:/out" \
      -v "$AKAR/scripts/ops/uji-nyata-paritas-s2.py:/h.py:ro" --entrypoint python "$IMG" -I /h.py "${@:2}"; }
f() { grep -E "PASS|FAIL|RINGKAS|dump|Traceback|Error"; }
j "$LAMA" dump /out/lama.json 2>&1 | f
j "$AKAR" dump /out/baru.json 2>&1 | f
j "$AKAR" banding /out/lama.json /out/baru.json 2>&1 | f
j "$AKAR" paritas 2>&1 | f
rm -rf "$O" "$E"
