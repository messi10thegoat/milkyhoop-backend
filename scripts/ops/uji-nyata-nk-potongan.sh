#!/usr/bin/env bash
# Pelari harness pagar NK potongan (pohon ini). DATABASE_URL lewat berkas 600 sementara. BACA saja (ROLLBACK).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.nkpot-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$AKAR/backend:/app/backend:ro" \
  -v "$AKAR/scripts/ops/uji-nyata-nk-potongan.py:/h.py:ro" --entrypoint python "$IMG" -I /h.py 2>&1 \
  | grep -E "PASS|FAIL|RINGKAS|keadaan|Traceback|Error"
rm -f "$E"
