#!/usr/bin/env bash
# Pelari harness DP-NETO: lama (pohon utama) -> baru (pohon ini) -> banding. DATABASE_URL lewat berkas 600 sementara.
# Lengan tulis kaos selalu ROLLBACK. grapgrap BACA saja.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; LAMA=${MH_MAIN:-/root/milkyhoop-dev}; G=milkyhoop-dev-api_gateway
O=$(mktemp -d /root/.dpneto-XXXX); E=$(mktemp /root/.dpneto-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
j() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$1/backend:/app/backend:ro" -v "$O:/out" \
      -v "$AKAR/scripts/ops/uji-nyata-dp-neto.py:/h.py:ro" --entrypoint python "$IMG" -I /h.py "${@:2}"; }
j "$LAMA" lama /out/lama.json 2>&1 | grep -E "PASS|FAIL|RINGKAS|lengan|Traceback|Error"
j "$AKAR" baru /out/baru.json 2>&1 | grep -E "PASS|FAIL|RINGKAS|lengan|Traceback|Error"
j "$AKAR" banding /out/lama.json /out/baru.json 2>&1
rm -rf "$O" "$E"
