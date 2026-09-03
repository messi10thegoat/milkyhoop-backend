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
#   ./scripts/ops/mh-recreate.sh frontend                 # nama service
#   ./scripts/ops/mh-recreate.sh milkyhoop-dev-frontend-1 # nama kontainer
#
# Sejak 2026-09-03 kedua bentuk diterima di KEDUA skrip. Sebelumnya
# mh-restart menuntut nama kontainer dan mh-recreate menuntut nama service —
# dua alat bersaudara dengan dua bahasa, dan yang salah menebak dihukum
# kegagalan alih-alih diterjemahkan.
#
# ⚠️ GERBANG "LIVE" WAJIB LEWAT PERMINTAAN NYATA KE KONTAINER — HTTP,
# atau gRPC untuk chatbot_service/ragcrud_service (lihat mh-lib.sh).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mh-lib.sh
. "$HERE/mh-lib.sh"

if [ $# -lt 1 ]; then
    echo "pemakaian: $0 <nama-service-compose|nama-kontainer>   (mis. api_gateway)" >&2
    mh_daftar_kontainer
    exit 2
fi

mh_resolve "$1"
echo "service: $MH_SVC   kontainer: $MH_CTR"

SEBELUM=$(mh_started_at "$MH_CTR")
echo "StartedAt sebelum : $SEBELUM"

mh_arsip_log "$MH_CTR" "-prerecreate"

cd "$TREE"
docker compose up -d --no-deps "$MH_SVC"

# Recreate mengganti kontainer, jadi resolve ULANG: nama/id bisa berubah.
mh_resolve "$MH_SVC"
SESUDAH=$(mh_started_at "$MH_CTR")
echo "StartedAt sesudah : $SESUDAH  (kontainer: $MH_CTR)"
if [ "$SEBELUM" = "$SESUDAH" ]; then
    echo "PERINGATAN: StartedAt tidak bergeser — compose mungkin menganggap" >&2
    echo "            kontainer sudah mutakhir dan TIDAK me-recreate apa pun." >&2
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
