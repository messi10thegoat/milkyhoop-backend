#!/usr/bin/env bash
# BUG-253 (diskon % per baris) -- harness klon: MEMAKAI harness impor 251 (I1-I7 membuktikan round trip ekspor/impor yang kini memuat kolom Diskon %) + harness harga minimum 040.
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-daftar-harga-diskon.sh. Tanpa migrasi baru; uji khusus diskon = resolve pada DB nyata (D1) di bawah.
set -u
D=$(dirname "$0")
POHON=${POHON:-/root/mh-dh-diskon-253} bash "$D/uji-salinan-daftar-harga-impor.sh"; r1=$?
POHON=${POHON:-/root/mh-dh-diskon-253} bash "$D/uji-salinan-daftar-harga-min.sh"; r2=$?
echo "rc=$(( r1 > r2 ? r1 : r2 )) (impor=$r1 min=$r2)"
