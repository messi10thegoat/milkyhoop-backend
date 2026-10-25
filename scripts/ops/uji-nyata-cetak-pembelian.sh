#!/usr/bin/env bash
# P2P cetak: uji nyata BACA-SAJA (kaos + grapgrap-manado). LAMA = pohon utama (get_bill_v2 acuan), BARU = worktree ini.
# MERAH=h0: BARU dengan satu kunci JSON dirusak -> H0 WAJIB merah (pembanding bisa gagal).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.cetakp-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.cetakp-out-XXXX)
trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e MERAH="${MERAH:-}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-cetak-pembelian.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$UTAMA" LAMA || rc=1
jalan "$AKAR" BARU; r=$?
if [ "${MERAH:-}" = h0 ]; then echo "rc_merah=$r (harus 1)"; [ $r = 1 ] || rc=1; else [ $r = 0 ] || rc=1; fi
echo "rc=$rc"; exit $rc
