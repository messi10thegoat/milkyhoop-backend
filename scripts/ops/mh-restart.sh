#!/usr/bin/env bash
# mh-restart.sh — ARSIPKAN log dulu, BARU restart kontainer.
#
# KENAPA BERKAS INI ADA
# 2026-09-03: owner melaporkan tiga giliran chat yang salah pada 02:27-02:28
# UTC. Saat diselidiki, lognya SUDAH TIDAK ADA — beberapa `docker restart`
# untuk keperluan lain sudah menggilasnya. Arsip terakhir berhenti 02:13.
# Penyelidikan terpaksa bersandar pada reproduksi, bukan bukti asli.
#
# Itu kejadian KEDUA dalam satu hari. Yang pertama: `docker compose up -d`
# me-recreate kontainer dan membuang jendela log yang memuat penghapusan
# faktur yang sedang diselidiki.
#
# Aturan "arsipkan dulu" sudah disepakati sesudah kejadian pertama, lalu
# tetap terlewat di kejadian kedua — karena ia langkah yang harus DIINGAT.
# Berkas ini memindahkannya dari ingatan ke alat: satu perintah yang tak bisa
# me-restart tanpa mengarsipkan.
#
# PAKAI INI, JANGAN `docker restart` LANGSUNG.
#
#   ./scripts/ops/mh-restart.sh milkyhoop-dev-api_gateway
#
set -euo pipefail

CTR="${1:-}"
if [ -z "$CTR" ]; then
    echo "pemakaian: $0 <nama-kontainer>" >&2
    exit 2
fi

# 2026-09-03: `docker container inspect`, bukan `docker inspect` — yang
# terakhir juga mencocokkan IMAGE bernama sama (lihat mh-recreate.sh).
if ! docker container inspect "$CTR" >/dev/null 2>&1; then
    echo "GAGAL: kontainer '$CTR' tidak ada." >&2
    exit 2
fi

DIR=/root/logs
mkdir -p "$DIR"
OUT="$DIR/${CTR}-$(date +%s).log"

# Arsip WAJIB berhasil sebelum restart. Kalau gagal, berhenti — kehilangan log
# jauh lebih mahal daripada menunda restart.
if ! docker logs "$CTR" > "$OUT" 2>&1; then
    echo "GAGAL: tak bisa mengarsipkan log '$CTR'. Restart DIBATALKAN." >&2
    rm -f "$OUT"
    exit 1
fi

echo "arsip : $OUT ($(stat -c%s "$OUT") byte)"
docker restart "$CTR"

# Tunggu sehat, jangan anggap restart = siap.
for i in $(seq 1 12); do
    if [ "$(docker inspect -f '{{.State.Running}}' "$CTR")" = "true" ]; then
        CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/healthz || echo 000)
        if [ "$CODE" = "200" ]; then
            echo "healthz: 200 (percobaan $i)"
            exit 0
        fi
    fi
    sleep 5
done
echo "PERINGATAN: healthz belum 200 sesudah ~60 detik — periksa manual." >&2
exit 1
