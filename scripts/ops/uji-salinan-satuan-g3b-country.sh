#!/usr/bin/env bash
# Harness gabungan Satuan G3-b + Gudang country_text (17 Nov 2026): harness SATUAN G3-b (V504 dipasang di salinan) lalu harness GUDANG; rc=0 (baris tunggal) HANYA bila KEDUANYA rc=0.
# Pakai: POHON=/root/<worktree> scripts/ops/uji-salinan-satuan-g3b-country.sh
set -u
D="$(cd "$(dirname "$0")" && pwd)"; gagal=0
for h in satuan-g3b gudang; do
  echo "=== harness $h"
  POHON="${POHON:?POHON wajib}" bash "$D/uji-salinan-$h.sh" > /tmp/g3bc-$h.$$ 2>&1
  sed 's/^rc=/rc-bagian=/' /tmp/g3bc-$h.$$
  grep -q "^rc=0$" /tmp/g3bc-$h.$$ || { echo "harness $h GAGAL"; gagal=1; }
  rm -f /tmp/g3bc-$h.$$
done
echo "rc=$gagal"; exit $gagal
