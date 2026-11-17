#!/usr/bin/env bash
# Pembungkus harness paket Persediaan BE-AB: jalankan uji-salinan-batch.sh + uji-salinan-be-b.sh (masing-masing SATU klon, dihapus). Cetak "rc=0" HANYA bila keduanya rc=0.
D="$(cd "$(dirname "$0")" && pwd)"
POHON=${POHON:-/root/mh-bea}; export POHON
bash "$D/uji-salinan-batch.sh" > /tmp/ab-batch.out 2>&1; r1=$(grep -E '^rc=[0-9]+ ' /tmp/ab-batch.out | tail -1 | sed 's/^rc=\([0-9]*\).*/\1/')
bash "$D/uji-salinan-be-b.sh" > /tmp/ab-beb.out 2>&1; r2=$(grep -E '^rc=[0-9]+ ' /tmp/ab-beb.out | tail -1 | sed 's/^rc=\([0-9]*\).*/\1/')
tail -40 /tmp/ab-batch.out; echo "-----"; tail -25 /tmp/ab-beb.out
echo "rc-batch=${r1:-99} rc-be-b=${r2:-99}"
if [ "${r1:-99}" = 0 ] && [ "${r2:-99}" = 0 ]; then echo "rc=0"; else echo "rc=1"; fi
