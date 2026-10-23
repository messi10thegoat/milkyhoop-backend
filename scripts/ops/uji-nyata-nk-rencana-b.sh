#!/usr/bin/env bash
# NK-RENCANA-B: pratinjau terbit/batal NK = penentu READ ONLY == tulis; penulis lama (pohon utama) == baru (pohon ini); net 0; kaos ditulis+ROLLBACK, kaos + grapgrap-manado BACA (koneksi read-only).
# MERAH=1 / MERAH_BOCOR=1 = kontrol merah (harus gagal). DSN lewat berkas env 600.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.nkrb-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.nkrb-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() {  # $1 pohon, $2 MODE, $3 NAMA
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" -e MERAH="${MERAH:-0}" -e MERAH_BOCOR="${MERAH_BOCOR:-0}" -e TENANT="${TENANT:-kaos-biru-konveksi}" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-nk-rencana-b.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
  return "${PIPESTATUS[0]}"
}
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
# pohon LAMA: hanya PEREKAM (lama.json); cek-nya informatif -- (B1) LAMA gagal by design: pesan blok pratinjau lama != galat tulis (cacat
# yang diperbaiki unit ini). Tanpa lama.json -> BANDING gagal (tak tersembunyi).
MERAH_BOCOR=0 jalan "$UTAMA" TULIS lama || echo "INFO pohon LAMA: ada cek gagal (diharapkan hanya B1)"
jalan "$AKAR" TULIS baru || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1
jalan "$AKAR" BANDING x || rc=1
if [ "${MERAH:-0}" != 1 ] && [ "${MERAH_BOCOR:-0}" != 1 ]; then
  TENANT=kaos-biru-konveksi jalan "$AKAR" BACA kaos || rc=1
  TENANT=grapgrap-manado jalan "$AKAR" BACA grapgrap || rc=1
fi
if [ "${MERAH_BOCOR:-0}" = 1 ]; then  # bersihkan baris kontrol merah (satu-satunya tulisan ber-commit, kunci khusus)
  docker run --rm --network milkyhoop_dev_network --env-file "$E" --entrypoint python "$IMG" -c \
    "import asyncio,asyncpg,os
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL']); print('BERSIH', await c.execute(\"DELETE FROM idempotency_keys WHERE key='TES-E2E-NKRB-BOCOR' AND tenant_id='kaos-biru-konveksi'\"))
    n=await c.fetchval(\"SELECT count(*) FROM idempotency_keys WHERE key='TES-E2E-NKRB-BOCOR'\"); print('SISA_BARIS_BOCOR', n); await c.close(); raise SystemExit(0 if n == 0 else 9)
asyncio.run(m())" || { echo "BERSIH GAGAL"; rc=9; }
fi
echo "rc=$rc"; exit $rc
