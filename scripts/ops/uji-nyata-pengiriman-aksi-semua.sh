#!/usr/bin/env bash
# Pengiriman aksi_semua (opsi A aditif): baris/detail + sintetis tumpang (kaos, ROLLBACK) + pill/summary LAMA (pohon utama) == BARU (pohon ini).
# MERAH=1 = kontrol merah (harus gagal). DSN lewat berkas env 600.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.sjas-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.sjas-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() {  # $1 pohon, $2 MODE, $3 NAMA
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e MERAH="${MERAH:-0}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-pengiriman-aksi-semua.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
  return "${PIPESTATUS[0]}"
}
rc=0
jalan "$UTAMA" JEPRET lama || rc=1
jalan "$AKAR" JEPRET baru || rc=1
jalan "$AKAR" BANDING x || rc=1
jalan "$AKAR" TULIS x || rc=1
echo "rc=$rc"; exit $rc
