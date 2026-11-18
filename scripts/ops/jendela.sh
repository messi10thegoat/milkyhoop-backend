#!/bin/bash
# Jendela rilis BE berparameter (18 Nov 2026; pola jendela-1118b/d): tunggu vonis rantai -> periksa -> [migrasi] -> window_item. Selesai SELALU dengan "SEMUA SELESAI".
# Pakai (env, semua WAJIB kecuali bertanda ?):
#   LOG=/root/logs/rantai-x.log  SHA=<sha>  BR=<cabang lokal>  WT=<worktree>  MASTER_SHA=<master yang diharapkan>  NAMA=<nama item>
#   PENANDA_BERKAS=services/x.py  PENANDA='def x('        (penanda window_item, kode BARU)
#   ? MIGRASI=Vnnn__x.sql  MIG_MARKER_BERKAS=services/y.py  MIG_MARKER_TEKS='def y('   (penanda kode yang SUDAH LIVE: apply_mig --require-marker memeriksa kode BERJALAN)
# Pemeriksaan (gagal = LEWAT, tanpa efek): rantai "rc-penuh-rantai=0" + SELESAI, HEAD == SHA, cabang lokal == BR (bukan detached), worktree bersih, master tak bergerak,
# cek-sibuk BEBAS, jeda apt 02:45-03:30 WIB. Urutan: migrasi (rc!=0 -> tak lanjut) -> window_item.
# Penggantian untuk uji (uji-jendela.sh): MH_MAIN APPLY_MIG WINDOW_ITEM CEK_SIBUK JAM_FIX (HHMM) MIG_BY MH_LOG_MAKS (detik tunggu log; kosong = tanpa batas) MH_BEBAS_PUTARAN.
: "${LOG:?}" "${SHA:?}" "${BR:?}" "${WT:?}" "${MASTER_SHA:?}" "${NAMA:?}" "${PENANDA_BERKAS:?}" "${PENANDA:?}"
MAIN=${MH_MAIN:-/root/milkyhoop-dev}; APPLY_MIG=${APPLY_MIG:-/root/apply_mig.sh}; WINDOW_ITEM=${WINDOW_ITEM:-/root/window_item.sh}; CEK_SIBUK=${CEK_SIBUK:-$MAIN/scripts/ops/cek-sibuk.sh}
MIGRASI=${MIGRASI:-}
jam() { [ -n "${JAM_FIX:-}" ] && echo "$JAM_FIX" || TZ=Asia/Jakarta date +%H%M; }
selesai() { echo "$1"; echo "SEMUA SELESAI $(date -u +%FT%TZ)"; exit 0; }
if [ -n "$MIGRASI" ]; then : "${MIG_MARKER_BERKAS:?MIGRASI butuh MIG_MARKER_BERKAS}" "${MIG_MARKER_TEKS:?MIGRASI butuh MIG_MARKER_TEKS}"; fi
t0=$(date +%s)
until [ -f "$LOG" ] && grep -q "rc-penuh-rantai=" "$LOG" && grep -q "SELESAI" "$LOG"; do
  [ -n "${MH_LOG_MAKS:-}" ] && [ $(( $(date +%s) - t0 )) -ge "$MH_LOG_MAKS" ] && selesai "$NAMA log rantai tak selesai -> lewat"
  sleep ${MH_LOG_JEDA:-60}
done
grep -q "rc-penuh-rantai=0\b" "$LOG" || selesai "$NAMA gerbang TIDAK lulus -> lewat"
[ "$(git -C "$WT" rev-parse --short=9 HEAD 2>/dev/null)" = "$(echo "$SHA" | cut -c1-9)" ] || selesai "$NAMA HEAD worktree berubah -> lewat"
[ "$(git -C "$WT" rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$BR" ] || selesai "$NAMA worktree bukan cabang lokal $BR -> lewat"
[ -z "$(git -C "$WT" status --short)" ] || selesai "$NAMA worktree kotor -> lewat"
[ "$(git -C "$MAIN" rev-parse --short=9 HEAD)" = "$(echo "$MASTER_SHA" | cut -c1-9)" ] || selesai "$NAMA master bergerak -> lewat"
bebas() { for i in $(seq 1 "${MH_BEBAS_PUTARAN:-720}"); do
  j=$(jam); if [ "$j" -ge 0245 ] && [ "$j" -lt 0330 ]; then sleep ${MH_JEDA:-60}; continue; fi
  "$CEK_SIBUK" 2>&1 | tail -1 | grep -q "^BEBAS" && return 0
  sleep ${MH_JEDA:-20}; done; return 1; }
if [ -n "$MIGRASI" ]; then
  bebas || selesai "$NAMA migrasi rc=TUNDA"
  echo "=== migrasi $MIGRASI mulai $(date -u +%FT%TZ)"
  MIG_BY=${MIG_BY:-MASTER} MIG_MAIN=$WT "$APPLY_MIG" "$MIGRASI" --require-marker "$MIG_MARKER_BERKAS" "$MIG_MARKER_TEKS"; r=$?
  echo "$MIGRASI rc=$r selesai $(date -u +%FT%TZ)"
  [ $r -eq 0 ] || selesai "$NAMA rc=LEWAT (migrasi gagal; window_item tidak dijalankan)"
fi
bebas || selesai "$NAMA rc=TUNDA"
echo "=== $NAMA mulai $(date -u +%FT%TZ)"
cd "$MAIN" && "$WINDOW_ITEM" "$NAMA" "$WT" "$BR" "$PENANDA_BERKAS" "$PENANDA"; echo "$NAMA rc=$? selesai $(date -u +%FT%TZ)"
echo "SEMUA SELESAI $(date -u +%FT%TZ)"
