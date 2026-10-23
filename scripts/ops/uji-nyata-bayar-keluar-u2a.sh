#!/usr/bin/env bash
# U2a: posting draf == buat-posted (byte), cek ulang sisa, tagihan draf 422 -- kaos saja, SEMUA tulisan di-ROLLBACK; net 0 diukur sesudah proses.
# Kontrol merah SELALU ikut: TULIS yang sama di pohon UTAMA (kode sebelum U2a) WAJIB gagal (p)/(r)/(q). Lewati dengan TANPA_MERAH=1 (mis.
# sesudah U2a live, pohon utama == pohon ini). DSN lewat berkas env 600.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; UTAMA=/root/milkyhoop-dev; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.u2a-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.u2a-out-XXXX); trap 'rm -rf "$E" "$O"' EXIT
docker exec "$G" printenv DATABASE_URL | sed 's/^/DATABASE_URL=/' > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() {  # $1 pohon, $2 MODE, $3 NAMA
  docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MODE="$2" -e NAMA="$3" \
    -v "$1/backend:/app/backend:ro" -v "$O:/out" -v "$AKAR/scripts/ops/uji-nyata-bayar-keluar-u2a.py:/h.py:ro" \
    -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 | grep -v -E "^(INFO|DEBUG|WARNING)"
  return "${PIPESTATUS[0]}"
}
rc=0
jalan "$AKAR" JEPRET sebelum || rc=1
echo "== HIJAU (pohon ini)"; jalan "$AKAR" TULIS x || rc=1
jalan "$AKAR" JEPRET sesudah || rc=1; jalan "$AKAR" BANDING sesudah || rc=1
if [ "${TANPA_MERAH:-0}" != 1 ]; then
  echo "== MERAH (pohon utama, kode lama) -- WAJIB gagal"
  if jalan "$UTAMA" TULIS x; then echo "MERAH TIDAK MERAH"; rc=1; else echo "MERAH OK (gagal sebagaimana mestinya)"; fi
  jalan "$AKAR" JEPRET sesudah_merah || rc=1; jalan "$AKAR" BANDING sesudah_merah || rc=1
fi
echo "rc=$rc"; exit $rc
