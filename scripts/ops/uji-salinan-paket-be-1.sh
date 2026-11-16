#!/usr/bin/env bash
# DRAF (17 Nov 2026, BELUM PERNAH DIJALANKAN -- ditulis di Mac tanpa akses server). HARNESS PEMBUNGKUS paket-be-1: menjalankan harness salinan tiap unit BERURUTAN (tiap unit membuat
# klon scratch sendiri lalu menghapusnya), memberi awalan "  | " pada keluaran anak (supaya baris rc= anak TIDAK dibaca gerbang sebagai rc= paket), dan mencetak SATU baris akhir
# rc=0 hanya bila SEMUA unit rc=0. Pakai: POHON=/root/mh-pbe1 scripts/ops/uji-salinan-paket-be-1.sh (cek-sibuk dulu).
set -u
D="$(cd "$(dirname "$0")" && pwd)"; POHON=${POHON:-/root/mh-pbe1}; export POHON
UNIT=(b249 b260 b259 b257 b272 k8w b268b)
GAGAL=()
for u in "${UNIT[@]}"; do
  echo "=== harness $u $(date +%H:%M:%S)"
  bash "$D/uji-salinan-$u.sh" 2>&1 | sed 's/^/  | /'
  rc=${PIPESTATUS[0]}
  echo "=== $u rc=$rc"
  [ "$rc" = 0 ] || GAGAL+=("$u")
done
if [ ${#GAGAL[@]} -eq 0 ]; then echo "SEMUA ${#UNIT[@]} harness LULUS"; echo "rc=0"; exit 0; fi
echo "harness GAGAL: ${GAGAL[*]}"; echo "rc=1"; exit 1
