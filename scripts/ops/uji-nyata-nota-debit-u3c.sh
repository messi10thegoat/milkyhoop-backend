#!/usr/bin/env bash
# Nota Debit U3c: daftar/detail/bundel/riwayat seragam -- kaos saja, 5 ND sintetis TES E2E lewat handler nyata, SEMUA di-ROLLBACK; net 0 sesudah proses.
# MERAH=1 / MERAH_BOCOR=1 = kontrol merah (harus gagal). DSN lewat berkas env 600.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.ndu3c-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.ndu3c-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() {  # $1 MODE, $2 NAMA
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$1" -e NAMA="$2" -e MERAH="${MERAH:-0}" -e MERAH_BOCOR="${MERAH_BOCOR:-0}" \
    -v "$AKAR/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-nota-debit-u3c.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
  return "${PIPESTATUS[0]}"
}
rc=0
jalan JEPRET sebelum || rc=1
jalan TULIS x || rc=1
jalan JEPRET sesudah || rc=1
jalan BANDING x || rc=1
if [ "${MERAH_BOCOR:-0}" = 1 ]; then
  docker run --rm --network milkyhoop_dev_network --env-file "$E" --entrypoint python "$IMG" -c \
    "import asyncio,asyncpg,os
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL']); print('BERSIH', await c.execute(\"DELETE FROM idempotency_keys WHERE key='TES-E2E-NDU3C-BOCOR' AND tenant_id='kaos-biru-konveksi'\"))
    n=await c.fetchval(\"SELECT count(*) FROM idempotency_keys WHERE key='TES-E2E-NDU3C-BOCOR'\"); print('SISA_BARIS_BOCOR', n); await c.close(); raise SystemExit(0 if n == 0 else 9)
asyncio.run(m())" || { echo "BERSIH GAGAL"; rc=9; }
fi
echo "rc=$rc"; exit $rc
