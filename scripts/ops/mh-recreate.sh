#!/usr/bin/env bash
# mh-recreate.sh — ARSIPKAN log dulu, BARU `compose up -d --no-deps`.
#
# Kembaran `mh-restart.sh`, untuk perubahan yang menuntut RECREATE (volume,
# `.env`, image). Recreate LEBIH merusak daripada restart: ia membuang seluruh
# filesystem kontainer DAN riwayat lognya sekaligus.
#
# 2026-09-03: `docker compose up -d api_gateway` untuk memasang satu mount
# membuang jendela log yang memuat penghapusan faktur yang sedang
# diselidiki. Bukti hilang demi satu baris konfigurasi.
#
# `--no-deps` WAJIB dan dipasang mati di sini: tanpa itu compose ikut
# me-recreate layanan di `depends_on` (postgres, chatbot_service,
# ragcrud_service, minio). Compose redis juga diketahui STALE — recreate
# redis memutus auth gateway (lihat memory `redis-misconf-capdrop`).
#
# PAKAI INI, JANGAN `docker compose up -d` LANGSUNG.
#
#   ./scripts/ops/mh-recreate.sh api_gateway
#
# Catatan: argumennya NAMA SERVICE di compose (`api_gateway`), sedangkan
# `mh-restart.sh` memakai NAMA KONTAINER (`milkyhoop-dev-api_gateway`).
set -euo pipefail

SVC="${1:-}"
if [ -z "$SVC" ]; then
    echo "pemakaian: $0 <nama-service-compose>   (mis. api_gateway)" >&2
    exit 2
fi

TREE=/root/milkyhoop-dev
CTR="milkyhoop-dev-${SVC}"
docker inspect "$CTR" >/dev/null 2>&1 || CTR="milkyhoop-dev-${SVC}-1"
if ! docker inspect "$CTR" >/dev/null 2>&1; then
    echo "GAGAL: tak menemukan kontainer untuk service '$SVC'." >&2
    exit 2
fi

DIR=/root/logs
mkdir -p "$DIR"
OUT="$DIR/${CTR}-$(date +%s)-prerecreate.log"
if ! docker logs "$CTR" > "$OUT" 2>&1; then
    echo "GAGAL: tak bisa mengarsipkan log '$CTR'. Recreate DIBATALKAN." >&2
    rm -f "$OUT"
    exit 1
fi
echo "arsip : $OUT ($(stat -c%s "$OUT") byte)"

cd "$TREE"
docker compose up -d --no-deps "$SVC"

for i in $(seq 1 12); do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/healthz || echo 000)
    if [ "$CODE" = "200" ]; then
        echo "healthz: 200 (percobaan $i)"
        exit 0
    fi
    sleep 5
done
echo "PERINGATAN: healthz belum 200 sesudah ~60 detik — periksa manual." >&2
exit 1
