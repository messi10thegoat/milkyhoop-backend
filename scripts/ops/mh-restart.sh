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
#   ./scripts/ops/mh-restart.sh api_gateway                 # nama service
#   ./scripts/ops/mh-restart.sh milkyhoop-dev-api_gateway   # nama kontainer
#
# ⚠️ GERBANG "LIVE" WAJIB LEWAT PERMINTAAN NYATA KE KONTAINER
# (HTTP, atau gRPC untuk chatbot_service/ragcrud_service).
# Skrip ini mencetak StartedAt sebelum/sesudah supaya restart yang TIDAK
# terjadi tak bisa menyamar jadi sukses. Harness in-process (TestClient)
# membaca kode SUMBER, bukan kontainer — ia pernah melaporkan 7/7 atas
# kontainer yang gagal di-restart. Lihat scripts/ops/mh-lib.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mh-lib.sh
. "$HERE/mh-lib.sh"

if [ $# -lt 1 ]; then
    echo "pemakaian: $0 <nama-service-compose|nama-kontainer>" >&2
    mh_daftar_kontainer
    exit 2
fi

mh_resolve "$1"
echo "service: $MH_SVC   kontainer: $MH_CTR"

SEBELUM=$(mh_started_at "$MH_CTR")
echo "StartedAt sebelum : $SEBELUM"

mh_arsip_log "$MH_CTR"

docker restart "$MH_CTR" >/dev/null

SESUDAH=$(mh_started_at "$MH_CTR")
echo "StartedAt sesudah : $SESUDAH"
if [ "$SEBELUM" = "$SESUDAH" ]; then
    echo "GAGAL: StartedAt tidak bergeser — kontainer TIDAK benar-benar restart." >&2
    exit 1
fi

# `set -e` akan membunuh skrip diam-diam kalau probe mengembalikan bukan-0,
# jadi hasilnya ditangani EKSPLISIT: 0 = terbukti sehat, 3 = tak ada probe
# (kontainer jalan tapi kesehatan TIDAK dibuktikan -- bukan kegagalan, tapi
# juga bukan klaim sehat), selain itu = gagal.
rc=0
mh_tunggu_probe "$MH_SVC" || rc=$?
case "$rc" in
    0) echo "HASIL: $MH_SVC terbukti melayani permintaan." ;;
    3) echo "HASIL: $MH_SVC berjalan, kesehatan TIDAK terbukti (tanpa probe)." ;;
    *) echo "HASIL: $MH_SVC TIDAK sehat." >&2 ;;
esac
exit "$rc"
