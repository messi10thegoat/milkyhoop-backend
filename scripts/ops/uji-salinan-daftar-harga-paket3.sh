#!/usr/bin/env bash
# Harness gabungan paket DH 3 (17 Nov 2026): jalankan harness SALIN (galat berkode S3b) lalu harness FILTER (BUG-254 + UKUR 500 barang), berurutan di salinan terpisah masing-masing.
# rc=0 (baris tunggal) HANYA bila KEDUANYA rc=0; rc bagian dicetak sebagai rc-bagian=.
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-daftar-harga-paket3.sh
set -u
D="$(cd "$(dirname "$0")" && pwd)"; gagal=0
for h in salin filter; do
  echo "=== harness $h"
  POHON="${POHON:?POHON wajib}" bash "$D/uji-salinan-daftar-harga-$h.sh" > /tmp/dh3-$h.$$ 2>&1
  sed 's/^rc=/rc-bagian=/' /tmp/dh3-$h.$$
  grep -q "^rc=0$" /tmp/dh3-$h.$$ || { echo "harness $h GAGAL"; gagal=1; }
  rm -f /tmp/dh3-$h.$$
done
echo "rc=$gagal"; exit $gagal
