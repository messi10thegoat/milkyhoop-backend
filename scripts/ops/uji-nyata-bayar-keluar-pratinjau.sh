#!/usr/bin/env bash
# PAY-PREVIEW U1: record_payment LAMA (pohon utama) vs BARU (pohon ini) + pratinjau == tulis + net 0. kaos saja, SEMUA tulisan di-ROLLBACK.
# MERAH=1 / MERAH_BOCOR=1 = kontrol merah (harus gagal). DSN lewat berkas env 600.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.bkp-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.bkp-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() {  # $1 pohon, $2 MODE, $3 NAMA
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e MERAH="${MERAH:-0}" -e MERAH_BOCOR="${MERAH_BOCOR:-0}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-bayar-keluar-pratinjau.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
  return "${PIPESTATUS[0]}"
}
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
MERAH_BOCOR=0 jalan "$UTAMA" TULIS lama || rc=1
jalan "$AKAR" TULIS baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
if [ "${MERAH_BOCOR:-0}" = 1 ]; then  # bersihkan baris kontrol merah (satu-satunya tulisan ber-commit, kunci khusus)
  docker run --rm --network milkyhoop_dev_network --env-file "$E" --entrypoint python "$IMG" -c \
    "import asyncio,asyncpg,os
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL']); print('BERSIH', await c.execute(\"DELETE FROM idempotency_keys WHERE key='TES-E2E-PAYPREVIEW-BOCOR' AND tenant_id='kaos-biru-konveksi'\")); await c.close()
asyncio.run(m())"
fi
echo "rc=$rc"; exit $rc
