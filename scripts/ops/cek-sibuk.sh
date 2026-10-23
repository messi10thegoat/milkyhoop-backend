#!/usr/bin/env bash
# BACKEND: cek "server sibuk" yang MEMBLOKIR (exit 1), bukan dicetak lalu diabaikan (pelanggaran 23 Okt: harness jalan saat jendela BACKEND2).
# Pemakaian: ssh root@... '/root/milkyhoop-dev/scripts/ops/cek-sibuk.sh (atau salinan /root/.backend-cek-sibuk.sh) && <perintah berat>'
# baris perintah yang MEMUAT skrip ini sendiri (ssh "cek-sibuk.sh && window_item.sh ...") bukan proses jendela -> dikecualikan
p=$(ps -eo args | grep -E "window_item|mh-gerbang-merge|apply_mig" | grep -v -e grep -e cek-sibuk | wc -l)
c=$(docker ps --format "{{.Names}}" | grep -v "^milkyhoop-dev-" | grep -v pgjourney | wc -l)
l=$(cut -d" " -f1 /proc/loadavg)
if [ "$p" -gt 0 ] || [ "$c" -gt 0 ] || awk "BEGIN{exit !($l >= 2)}"; then
  echo "SIBUK: proses jendela=$p kontainer sekali-pakai=$c load=$l -- BATAL"; exit 1
fi
echo "BEBAS: proses=0 kontainer=0 load=$l"
