#!/usr/bin/env bash
# Uji KERING mh-reload.sh terhadap app MINI (image gateway yang sama, uvicorn 0.35, tini, 2 worker,
# startup 8 dtk tiruan, port 127.0.0.1:18011, TANPA DB/env). Tak menyentuh kontainer produksi.
# Kasus: (0) kontrol merah: sinyal yang diabaikan -> LIVE-RED; (1) perubahan normal di bawah beban ->
# LULUS + 0 permintaan gagal; (2) impor CRASH -> LIVE-RED <=30 dtk + saran; (3) galat sintaks ->
# LIVE-RED -- dijalankan TEPAT sesudah pemulihan kasus 2 = regresi hijau-palsu PULIH 26 Sep; (4) modul yang diimpor HANYA oleh router berubah -> SEMUA worker menyajikan kode baru.
# Keluar 0 hanya bila semua kasus sesuai harapan.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/mh-reload.sh"; C=mh-reload-uji; D=$(mktemp -d /tmp/mhreload.XXXX); URL=http://127.0.0.1:18011/healthz
gagal=0; cek(){ if [ "$1" = "$2" ]; then echo "  OK: $3"; else echo "  SALAH: $3 (dapat $1, harap $2)"; gagal=1; fi; }
cat > "$D/modul_x.py" <<'P'
VERSI = "V1"
P
cat > "$D/rute.py" <<'P'
import os
from fastapi import APIRouter
import modul_x
router = APIRouter()
@router.get("/versi")
async def versi():
    return {"v": modul_x.VERSI, "pid": os.getpid()}
P
cat > "$D/app.py" <<'P'
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
import rute
@asynccontextmanager
async def lifespan(app):
    await asyncio.sleep(8)
    yield
app = FastAPI(lifespan=lifespan)
app.include_router(rute.router)
@app.get("/healthz")
async def h():
    return {}
P
bersih(){ docker rm -f $C >/dev/null 2>&1; rm -rf "$D" "$D".out "$D".beban "$D".v; rm -f /root/logs/${C}-*.log; }   # arsip log uji tak ikut menumpuk
trap bersih EXIT
docker rm -f $C >/dev/null 2>&1
docker run -d --name $C -p 127.0.0.1:18011:8000 -v "$D":/uji:ro -w /uji --entrypoint tini \
  milkyhoop-dev-api_gateway:latest -- python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2 >/dev/null || exit 2
for i in $(seq 1 30); do [ "$(curl -s -m 2 -o /dev/null -w %{http_code} $URL)" = 200 ] && break; sleep 1; done
jalan(){ local t0=$(date +%s); "$@" > "$D.out" 2>&1; local rc=$?; DET=$(( $(date +%s) - t0 )); RC=$rc; }

echo "== (0) KONTROL MERAH: sinyal WINCH (diabaikan uvicorn)"
MH_RELOAD_SINYAL=WINCH jalan "$R" $C --probe $URL --batas 15
cek "$RC" 8 "rc LIVE-RED"; grep -q "tak siap dalam" "$D.out"; cek $? 0 "pesan 'tak siap'"

echo "== (1) perubahan normal di bawah beban"
sed -i 's/V1/V1b/' "$D/modul_x.py"
: > "$D.beban"; ( end=$(( $(date +%s) + 30 )); while [ $(date +%s) -lt $end ]; do curl -s -m 30 -o /dev/null -w "%{http_code}\n" $URL >> "$D.beban" & sleep 0.25; done; wait ) &
BP=$!; sleep 3
jalan "$R" $C --probe $URL --batas 30; wait $BP
cek "$RC" 0 "rc LULUS (${DET}s)"; grep -q "^RELOAD|" "$D.out"; cek $? 0 "baris RELOAD"
cek "$(grep -vc '^200$' "$D.beban")" 0 "permintaan gagal di bawah beban (dari $(wc -l < "$D.beban"))"

echo "== (2) impor CRASH"
printf 'raise RuntimeError("uji crash impor")\nVERSI = "V2"\n' > "$D/modul_x.py"
jalan "$R" $C --probe $URL --batas 30
cek "$RC" 8 "rc LIVE-RED"; [ "$DET" -le 30 ]; cek $? 0 "LIVE-RED dalam ${DET}s (<=30)"
grep -q "SARAN:.*mh-restart" "$D.out"; cek $? 0 "saran fallback mh-restart"
grep -q "uji crash impor" "$D.out"; cek $? 0 "traceback penyebab ikut dicetak"
echo 'VERSI = "V1c"' > "$D/modul_x.py"; jalan "$R" $C --probe $URL --batas 30; cek "$RC" 0 "pulih sesudah kode diperbaiki (${DET}s)"
grep -q "^PULIH|" "$D.out"; cek $? 0 "pulih lewat PULIH (gateway TIDAK sehat -> tanpa HUP)"

echo "== (3) galat SINTAKS"
echo 'VERSI = ("V2"' > "$D/modul_x.py"
jalan "$R" $C --probe $URL --batas 30
cek "$RC" 8 "rc LIVE-RED"; [ "$DET" -le 30 ]; cek $? 0 "LIVE-RED dalam ${DET}s (<=30)"
grep -q "SyntaxError" "$D.out"; cek $? 0 "SyntaxError dicetak"
echo 'VERSI = "V1d"' > "$D/modul_x.py"; jalan "$R" $C --probe $URL --batas 30; cek "$RC" 0 "pulih (${DET}s)"
grep -qE "^(PULIH|RELOAD)\|" "$D.out"; cek $? 0 "pulih lewat $(grep -oE '^(PULIH|RELOAD)' "$D.out")"

echo "== (4) modul yang diimpor HANYA oleh router"
echo 'VERSI = "V9"' > "$D/modul_x.py"
jalan "$R" $C --probe $URL --batas 30; cek "$RC" 0 "rc LULUS"
grep -q "^RELOAD|" "$D.out"; cek $? 0 "jalur HUP (bukan PULIH)"
baru=$(grep "^worker baru" "$D.out" | sed 's/.*): //; s/ *healthz.*//' | tr ' ' '\n' | sort)
for i in $(seq 1 40); do curl -s -m 5 http://127.0.0.1:18011/versi; echo; done > "$D.v"
cek "$(grep -vc '"v":"V9"' "$D.v")" 0 "respons BUKAN V9 dari 40"
pids=$(grep -o '"pid":[0-9]*' "$D.v" | cut -d: -f2 | sort -u | wc -l)
cek "$pids" 2 "kedua worker melayani (pid berbeda)"
echo; [ $gagal -eq 0 ] && echo "LULUS: uji kering mh-reload 5 kasus" || echo "GAGAL: uji kering mh-reload"
exit $gagal
