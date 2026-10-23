#!/usr/bin/env bash
# Posting tagihan <-> GRN: flag nyala HANYA di transaksi ROLLBACK (putusan MASTER), kaos saja; flag mati byte-identik LAMA (pohon utama) == BARU;
# net 0 sesudah proses (termasuk tenant_features). MERAH=1 / MERAH_BOCOR=1 = kontrol merah (harus gagal). DSN lewat berkas env 600.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.tgrn-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.tgrn-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() {  # $1 pohon, $2 MODE, $3 NAMA, $4 FLAG_MATI
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e FLAG_MATI="${4:-0}" -e MERAH="${MERAH:-0}" \
    -e MERAH_BOCOR="${MERAH_BOCOR:-0}" -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-tagihan-grn.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
  return "${PIPESTATUS[0]}"
}
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
MERAH=0 MERAH_BOCOR=0 jalan "$UTAMA" TULIS mati_lama 1 || rc=1
MERAH_BOCOR=0 jalan "$AKAR" TULIS mati_baru 1 || rc=1
jalan "$AKAR" TULIS x 0 || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
if [ "${MERAH_BOCOR:-0}" = 1 ]; then
  docker run --rm --network milkyhoop_dev_network --env-file "$E" --entrypoint python "$IMG" -c \
    "import asyncio,asyncpg,os
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL']); print('BERSIH', await c.execute(\"DELETE FROM idempotency_keys WHERE key='TES-E2E-TAGIHAN-GRN-BOCOR' AND tenant_id='kaos-biru-konveksi'\"))
    n=await c.fetchval(\"SELECT count(*) FROM idempotency_keys WHERE key='TES-E2E-TAGIHAN-GRN-BOCOR'\"); print('SISA_BARIS_BOCOR', n); await c.close(); raise SystemExit(0 if n == 0 else 9)
asyncio.run(m())" || { echo "BERSIH GAGAL"; rc=9; }
fi
echo "rc=$rc"; exit $rc
