#!/usr/bin/env bash
# Bayar banyak faktur: uji nyata BACA-SAJA kaos + grapgrap-manado. LAMA = pohon utama (pembanding), BARU = worktree ini.
# MERAH=urut: kunci tertua dibalik -> BARU WAJIB merah (pembanding bisa gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.bbf-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.bbf-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e MERAH="${MERAH:-}" \
    -v "$1/backend:/app/backend:ro" -v "$AKAR/scripts/ops/uji-nyata-bayar-banyak.py:/h.py:ro" -v "$O:/out" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$UTAMA" LAMA || rc=1
jalan "$AKAR" BARU; r=$?
if [ -n "${MERAH:-}" ]; then echo "rc_merah=$r (harus 1)"; [ $r = 1 ] || rc=1; else [ $r = 0 ] || rc=1; fi
echo "rc=$rc"; exit $rc
