#!/usr/bin/env bash
# PAKET RANTAI Daftar Harga: menjalankan SEMUA harness klon berurutan pada pohon yang sama (impor 251, min 040, diskon 253, berjangka 245, riwayat 252).
# Keluaran tiap harness DIINDENT ("  | ") supaya hanya baris akhir "rc=N" pembungkus ini yang dibaca gerbang. rc=0 hanya bila SEMUA harness rc=0.
set -u
D=$(dirname "$0"); pecah=0; hasil=""
for h in impor min diskon berjangka riwayat; do
  echo "=== harness $h $(date +%H:%M)"
  out=$(POHON="${POHON:-/root/mh-dh-riwayat-252}" bash "$D/uji-salinan-daftar-harga-$h.sh" 2>&1); echo "$out" | sed 's/^/  | /'
  rc=$(echo "$out" | grep -E "^rc=[0-9]+$" | tail -1 | cut -d= -f2); rc=${rc:-99}
  hasil="$hasil $h=$rc"; [ "$rc" = 0 ] || pecah=1
done
echo "harness:$hasil"
echo "rc=$pecah"
