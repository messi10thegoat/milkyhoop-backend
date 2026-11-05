#!/usr/bin/env bash
# Uji NYATA alur GROSIR ujung-ke-ujung (lajur 3/4: flag kaos dibuat seperti nyawit DI DALAM rollback) (kaos, transaksi luar ROLLBACK; grapgrap md5). Kode = pohon master (/root/milkyhoop-dev deploy/master)
# kecuali W=<worktree>. Lajur 1 HARUS lulus (rc 0); lajur 2 SABOT=sd (konversi diabaikan di satuan_dasar) HARUS merah (rc 1).
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; G=milkyhoop-dev-api_gateway
E=$(mktemp /root/.grosir-env-XXXX); chmod 600 "$E"; O=$(mktemp -d /root/.grosir-out-XXXX); MP=$(mktemp -d /root/.grosir-kode-XXXX)
trap 'rm -rf "$E" "$O" "$MP"' EXIT
printf "URL=%s\n" "$(docker exec "$G" printenv DATABASE_URL)" > "$E"
if [ -n "${W:-}" ]; then K="$W/backend"; else git -C /root/milkyhoop-dev archive deploy/master backend | tar -x -C "$MP"; K="$MP/backend"; fi
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
echo "kode=${W:-deploy/master $(git -C /root/milkyhoop-dev rev-parse --short deploy/master)} harness=$(git -C "$AKAR" rev-parse --short HEAD)"
jalan() { timeout 600 docker run --rm --network milkyhoop_dev_network --env-file "$E" -e SABOT="$1" -e PROFIL="${3:-}" -e POLIS="${4:-}" -v "$K:/app/backend:ro" \
    -v "$AKAR/scripts/ops/uji-nyata-grosir.py:/h.py:ro" -v "$O:/out" -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py "/out/$2" \
    < /dev/null 2>&1 | grep -E "^(OK|GAGAL|SELESAI|SABOTASE|INFO|==|  L|Traceback)|Error" | cut -c1-900; return "${PIPESTATUS[0]}"; }
rc=0
echo "=== LAJUR 1 (harus lulus)"; jalan "" grosir.json; r=$?; echo "rc=$r"; [ $r = 0 ] || rc=1
echo "=== LAJUR 2 SABOT=sd (harus merah)"; jalan sd sabot.json; r=$?; echo "rc_merah=$r (harus 1)"; [ $r = 1 ] || rc=1
echo "=== LAJUR 3a PROFIL=nyawit POLIS=invoice (kirim otomatis saat faktur, harus lulus)"; jalan "" nyawit-inv.json nyawit invoice; r=$?; echo "rc=$r"; [ $r = 0 ] || rc=1
echo "=== LAJUR 3b PROFIL=nyawit POLIS=delivery (kirim dari faktur, harus lulus)"; jalan "" nyawit-del.json nyawit delivery; r=$?; echo "rc=$r"; [ $r = 0 ] || rc=1
echo "=== LAJUR 4 PROFIL=nyawit POLIS=invoice SABOT=sd (harus merah)"; jalan sd nyawit-sabot.json nyawit invoice; r=$?; echo "rc_merah=$r (harus 1)"; [ $r = 1 ] || rc=1
echo "RC=$rc"; exit $rc
