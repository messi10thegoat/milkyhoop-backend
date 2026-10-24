#!/usr/bin/env bash
# AS-G2: idempotensi aksi Pesanan Penjualan (konfirmasi/batal/tutup/hapus); lama vs baru; kaos ROLLBACK; net 0.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.g2-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.g2-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e MERAH="${MERAH:-0}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-as-g2.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
MERAH=0 jalan "$UTAMA" TULIS lama || rc=1
MERAH=0 jalan "$UTAMA" TULIS lama2 || rc=1   # jalan LAMA kedua: bukti medan tak-deterministik
jalan "$AKAR" TULIS baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
