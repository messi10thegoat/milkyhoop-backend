#!/usr/bin/env bash
# mh-reload.sh — muat ulang KODE gateway tanpa menutup port (SIGHUP ke parent uvicorn).
#
# KENAPA (diukur 25 Sep 2026): `docker restart` menutup port selama STARTUP (10–25 dtk) -> nginx 502
# ke pengguna tiap jendela (0–15 per jendela). uvicorn 0.35 multiproses: SIGHUP -> restart_all
# mengganti worker satu per satu sementara PARENT memegang socket -> permintaan ANTRE, tak ditolak.
# Uji kering (app mini, image sama): restart 71/153 gagal, HUP 0/153 gagal (antre maks 16,6 dtk).
#
# BATAS: hanya perubahan KODE (bind-mount). Perubahan .env/image -> mh-restart.sh / mh-recreate.sh.
# StartedAt TIDAK bergeser pada HUP -> BUKTI = (1) semua PID worker lama hilang, jumlah worker baru
# sama; (2) "Received SIGHUP" di log sejak T0; (3) healthz 200 dengan batas 5 dtk; (4) tak ada
# "Child process ... died" / "startup failed" sejak T0; (5) tak ada proses yatim (anak worker lama,
# mis. prisma query-engine, yang parent-nya bukan worker hidup); (6) opsional penanda di berkas.
# Gagal salah satu dalam BATAS dtk (default 30) -> LIVE-RED (rc 8) + saran fallback mh-restart.
#
# Pemakaian: mh-reload.sh <service|kontainer> [--probe URL] [--batas DETIK] [--penanda <rel-app> <teks>]
# Keluar: 0 LULUS · 2 argumen/alat · 3 bukan multiproses (HUP tak berlaku) · 8 LIVE-RED · 9 penanda
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/mh-lib.sh"
set +e   # mh-lib memasang -e; skrip ini memeriksa rc sendiri

[ $# -ge 1 ] || { echo "pemakaian: $0 <service|kontainer> [--probe URL] [--batas DETIK] [--penanda <rel-app> <teks>]" >&2; exit 2; }
mh_resolve "$1"; shift
PROBE=""; BATAS=30; PF=""; PT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --probe) PROBE=$2; shift 2 ;;
        --batas) BATAS=$2; shift 2 ;;
        --penanda) PF=$2; PT=$3; shift 3 ;;
        *) echo "argumen tak dikenal: $1" >&2; exit 2 ;;
    esac
done
[ -n "$PROBE" ] || PROBE=$(mh_probe_url "$MH_SVC")
[ -n "$PROBE" ] || { echo "GAGAL: tak ada URL probe untuk '$MH_SVC' (pakai --probe)." >&2; exit 2; }

# pid ppid args -> baris proses kontainer
proses() { docker top "$MH_CTR" -eo pid,ppid,args 2>/dev/null | tail -n +2; }
induk_uvicorn() { proses | awk '$3 ~ /python/ && / -m uvicorn / {print $1}' | head -1; }
pekerja() { local p; p=$(induk_uvicorn); [ -n "$p" ] && proses | awk -v p="$p" '$2==p && /multiprocessing\.spawn/ {print $1}' | sort; }
# yatim SUNGGUHAN = proses yang di-REPARENT ke tini (PID 1 kontainer) karena induknya (worker lama) mati,
# selain parent uvicorn itu sendiri. Proses `docker exec` (PPID = containerd-shim di LUAR kontainer) dan
# healthcheck BUKAN yatim. 26 Sep 2026: definisi lama ("PPID bukan worker hidup") menghitung proses ukur
# `docker exec python -X importtime` sebagai yatim -> LIVE-RED palsu -> restart nyata (jendela tInv).
yatim() {
    local p t
    p=$(induk_uvicorn)
    t=$(proses | awk '$3 ~ /tini/ {print $1}' | head -1)
    [ -n "$t" ] || return 0
    proses | awk -v p="$p" -v t="$t" '$2==t && $1!=p {print}'
}
merah() {
    echo "LIVE-RED: $*" >&2
    echo "  log sejak T0 (galat):" >&2
    docker logs --since "$T0" "$MH_CTR" 2>&1 | grep -E "Traceback|Error|died|failed|SIGHUP" | tail -12 | sed 's/^/    /' >&2
    echo "  SARAN: perbaiki/kembalikan kode lalu 'mh-reload.sh $MH_SVC', ATAU fallback 'scripts/ops/mh-restart.sh $MH_SVC'" >&2
    echo "         (worker yang gagal impor diulang terus oleh parent; permintaan MENGGANTUNG sampai timeout nginx)." >&2
    exit 8
}

# N = jumlah worker yang DIKONFIGURASI (--workers pada parent), bukan yang kebetulan hidup sekarang.
N=$(proses | awk '/ -m uvicorn / && !/tini/' | grep -oE -- '--workers[ =][0-9]+' | grep -oE '[0-9]+' | head -1)
echo "kontainer: $MH_CTR   probe: $PROBE   batas: ${BATAS}s   worker dikonfigurasi: ${N:-?}"
[ -n "$N" ] && [ "$N" -ge 1 ] || { echo "GAGAL: parent uvicorn tanpa --workers N — HUP tak berlaku; pakai mh-restart.sh." >&2; exit 3; }
LAMA=$(pekerja); NL=$(echo "$LAMA" | grep -c . || true)
echo "worker lama ($NL): $(echo $LAMA)"

# Keadaan RUSAK = TIDAK sehat SEKARANG (worker hidup != N ATAU healthz != 200 dalam 5 dtk). Parent
# me-respawn worker yang mati dengan impor SEGAR -> kode yang sudah diperbaiki termuat SENDIRI; HUP tak
# perlu. Mode PULIH: tunggu N worker + healthz + 5 dtk tanpa "died".
# 26 Sep: heuristik lama "ada died <20 dtk" memberi HIJAU PALSU — gateway SEHAT (worker lama) dengan
# berkas rusak masuk PULIH, lolos tanpa HUP. Bila SEHAT sekarang -> SELALU jalur HUP + bukti PID.
H0=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "$PROBE" 2>/dev/null) || H0=000
if [ "$NL" != "$N" ] || [ "$H0" != "200" ]; then
    echo "KEADAAN RUSAK terdeteksi (worker hidup $NL/$N, healthz $H0) -> mode PULIH tanpa HUP"
    T0=$(date -u +%Y-%m-%dT%H:%M:%S); AKHIR=$(( $(date +%s) + BATAS ))
    while :; do
        NB=$(pekerja | grep -c . || true)
        h=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$PROBE" 2>/dev/null) || h=000
        mati=$(docker logs --since 5s "$MH_CTR" 2>&1 | grep -cE "Child process \[[0-9]+\] died|Application startup failed" || true)
        [ "$NB" = "$N" ] && [ "$h" = "200" ] && [ "$mati" = "0" ] && break
        [ "$(date +%s)" -lt "$AKHIR" ] || merah "tak PULIH dalam ${BATAS}s (worker=$NB/$N, healthz=$h, mati 5 dtk terakhir=$mati) — kode di berkas masih rusak?"
        sleep 1
    done
    echo "PULIH|$MH_CTR|worker=$(pekerja | tr '\n' ',')|healthz=$h|$(( $(date +%s) - $(date -d "$T0" +%s) ))s"
    exit 0
fi
Y0=$(yatim); [ -z "$Y0" ] || { echo "CATATAN: sudah ada proses yatim SEBELUM reload:"; echo "$Y0" | sed 's/^/  /'; }
SEBELUM=$(mh_started_at "$MH_CTR")

T0=$(date -u +%Y-%m-%dT%H:%M:%S)
mh_arsip_log "$MH_CTR" || exit 2
SINYAL=${MH_RELOAD_SINYAL:-HUP}   # hanya untuk KONTROL MERAH uji (sinyal yang diabaikan uvicorn harus LIVE-RED)
docker kill -s "$SINYAL" "$MH_CTR" >/dev/null || { echo "GAGAL: docker kill -s $SINYAL" >&2; exit 2; }
echo "${SINYAL} dikirim ${T0}Z"

AKHIR=$(( $(date +%s) + BATAS ))
while :; do
    LOGT=$(docker logs --since "$T0" "$MH_CTR" 2>&1)
    if echo "$LOGT" | grep -qE "Child process \[[0-9]+\] died|Application startup failed"; then
        merah "worker baru MATI saat start (impor/startup gagal)"
    fi
    BARU=$(pekerja); NB=$(echo "$BARU" | grep -c . || true)
    sisa_lama=$(comm -12 <(echo "$LAMA") <(echo "$BARU") | grep -c . || true)
    h=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$PROBE" 2>/dev/null) || h=000
    if [ "$sisa_lama" = "0" ] && [ "$NB" = "$N" ] && [ "$h" = "200" ] && echo "$LOGT" | grep -q "Received SIGHUP"; then
        break
    fi
    [ "$(date +%s)" -lt "$AKHIR" ] || merah "tak siap dalam ${BATAS}s (worker lama tersisa=$sisa_lama, worker=$NB/$N, healthz=$h, SIGHUP-di-log=$(echo "$LOGT" | grep -c 'Received SIGHUP'))"
    sleep 1
done
DETIK=$(( $(date +%s) - $(date -d "$T0" +%s) ))
[ "$(mh_started_at "$MH_CTR")" = "$SEBELUM" ] || echo "CATATAN: StartedAt bergeser — kontainer ikut restart (bukan HUP murni)."
sleep 3   # beri waktu anak worker lama ditutup sebelum menghitung yatim
Y1=$(yatim)
if [ -n "$Y1" ] && [ "$Y1" != "$Y0" ]; then
    echo "$Y1" | sed 's/^/  yatim: /' >&2
    merah "proses YATIM sesudah reload (anak worker lama tak ikut mati) — bocor tiap reload"
fi
echo "worker baru ($NB): $(echo $BARU)   healthz=$h   ${DETIK}s"
if [ -n "$PF" ]; then
    m=$(docker exec "$MH_CTR" grep -cF -- "$PT" "/app/backend/api_gateway/app/$PF" 2>/dev/null || true); m=$(echo "$m" | head -1)
    [ "${m:-0}" -ge 1 ] || { echo "LIVE-RED: penanda tak ada di berkas kontainer: $PF" >&2; exit 9; }
    echo "penanda: $m"
fi
echo "RELOAD|$MH_CTR|$(echo $LAMA | tr ' ' ',')->$(echo $BARU | tr ' ' ',')|healthz=$h|${DETIK}s"
exit 0
