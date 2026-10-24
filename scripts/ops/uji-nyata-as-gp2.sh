#!/usr/bin/env bash
# AS-GP2: idempotensi aksi Faktur Pembelian + kunci baris ubah v2. TULIS lama/baru = ROLLBACK (net 0); KONK = COMMIT 'TES E2E' dua koneksi
# (izin MASTER 24 Okt) lalu void lewat aplikasi; net uang 0 (GL/AP) + delta tepat dilaporkan.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.gp2-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.gp2-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e MERAH="${MERAH:-0}" -e LEWATI_KONK="${LEWATI_KONK:-0}" -e TANPA_VOID="${TANPA_VOID:-0}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-as-gp2.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"; return "${PIPESTATUS[0]}"; }
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
MERAH=0 jalan "$UTAMA" TULIS lama || rc=1
jalan "$AKAR" TULIS baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
[ "${MERAH:-0}" = 1 ] || [ "${LEWATI_KONK:-0}" = 1 ] || jalan "$AKAR" KONK k || rc=1
jalan "$AKAR" BANDING x || rc=1
echo "rc=$rc"; exit $rc
