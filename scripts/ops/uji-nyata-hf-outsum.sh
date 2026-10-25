#!/usr/bin/env bash
# HOTFIX outstanding-summary: eksekusi NYATA READ ONLY. lama (pohon utama/master) WAJIB merah sintaks; baru WAJIB hijau + angka == fungsi.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.hfos-env-XXXX); chmod 600 "$E"; trap 'rm -f "$E"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" \
    -v "$1/backend:/app/backend:ro" -v "$AKAR/scripts/ops/uji-nyata-hf-outsum.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
echo "== lama ($UTAMA) =="; jalan "$UTAMA" lama || rc=1
echo "== baru ($AKAR) =="; jalan "$AKAR" baru || rc=1
echo "rc=$rc"; exit $rc
