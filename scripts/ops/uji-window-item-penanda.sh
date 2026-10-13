#!/bin/bash
# Uji W-20261014-WINDOW-PENANDA: window_item.sh menormalisasi path penanda dan BERHENTI SEBELUM gerbang bila berkas/teks
# penanda tak ada di worktree. Kotak pasir: pohon utama tiruan (clone --shared) dengan remote `deploy` = repo bare LOKAL,
# gerbang/restart = stub yang mencatat bila dipanggil. Nol sentuhan ke remote/gateway/pohon utama sungguhan.
# Pemakaian: uji-window-item-penanda.sh [skrip window_item yang diuji]   (bawaan: scripts/ops/window_item.sh di repo ini)
# Kontrol merah: jalankan dengan salinan skrip yang pemeriksaan awalnya dicabut -> WAJIB MERAH.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
SK=${1:-$REPO/scripts/ops/window_item.sh}
K=$(mktemp -d /tmp/uji-winpenanda.XXXX)
trap 'git -C "$K/main" worktree remove --force "$K/w" >/dev/null 2>&1; rm -rf "$K"' EXIT
gagal=0
cek(){ if [ "$1" = 0 ]; then echo "PASS $2"; else echo "FAIL $2${3:+ | $3}"; gagal=$((gagal+1)); fi; }

git init -q --bare "$K/remote.git"
git clone -q --shared "$REPO" "$K/main"
git -C "$K/main" checkout -q -B master HEAD
git -C "$K/main" remote add deploy "$K/remote.git"
git -C "$K/main" push -q deploy master && git -C "$K/main" fetch -q deploy
mkdir -p "$K/main/scripts/ops"
for s in mh-gerbang-merge.sh mh-restart.sh mh-reload.sh; do
  printf '#!/bin/bash\necho DIPANGGIL >> %s/dipanggil.txt\necho "STUB %s"; exit 1\n' "$K" "$s" > "$K/main/scripts/ops/$s"
done
git -C "$K/main" worktree add -q "$K/w" -b uji/penanda master
# berkas penanda MILIK uji (tak bergantung isi kode produk), di-commit -> worktree bersih
F=backend/api_gateway/app/routers/_uji_penanda_window.py
T="PENANDA_UJI_WINDOW = \"$$\""
echo "$T" > "$K/w/$F" && git -C "$K/w" add "$F" && git -C "$K/w" -c user.email=uji@milkyhoop -c user.name=UJI commit -q -m uji
git -C "$K/w" push -q deploy uji/penanda   # lease push window_item butuh cabang di remote (tiruan)

jalan(){  # $1 path, $2 teks, $3 CEK_SAJA -> keluaran di $K/out, rc di $K/rc
  rm -f "$K/dipanggil.txt"
  ( MH_MAIN="$K/main" CEK_SAJA="$3" bash "$SK" UJI "$K/w" uji/penanda "$1" "$2" > "$K/out" 2>&1; echo $? > "$K/rc" )
}

# 1-4: empat bentuk path -> satu path relatif app/
for p in "routers/_uji_penanda_window.py" "backend/api_gateway/app/routers/_uji_penanda_window.py" "app/routers/_uji_penanda_window.py" "/app/backend/api_gateway/app/routers/_uji_penanda_window.py"; do
  jalan "$p" "$T" 1
  [ "$(cat "$K/rc")" = 0 ] && grep -qx "PENANDA-OK routers/_uji_penanda_window.py" "$K/out"; cek $? "normalisasi '$p'" "$(tail -1 "$K/out")"
done
# 5: berkas palsu, jalan SUNGGUHAN (tanpa CEK_SAJA) -> berhenti sebelum gerbang
jalan "routers/tak_ada_sama_sekali.py" "$T" 0
[ "$(cat "$K/rc")" = 3 ] && grep -q "REFUSE: berkas penanda tak ada" "$K/out" && [ ! -f "$K/dipanggil.txt" ]
cek $? "berkas palsu: REFUSE rc=3 SEBELUM gerbang (stub tak dipanggil)" "rc=$(cat "$K/rc") $(tail -1 "$K/out")"
# 6: berkas benar, teks penanda palsu -> berhenti sebelum gerbang
jalan "routers/_uji_penanda_window.py" "PENANDA_PALSU_$$" 0
[ "$(cat "$K/rc")" = 3 ] && grep -q "REFUSE: teks penanda tak ada" "$K/out" && [ ! -f "$K/dipanggil.txt" ]
cek $? "teks palsu: REFUSE rc=3 SEBELUM gerbang" "rc=$(cat "$K/rc") $(tail -1 "$K/out")"
# 7: penanda kosong
jalan "" "" 0
[ "$(cat "$K/rc")" = 3 ] && [ ! -f "$K/dipanggil.txt" ]; cek $? "penanda kosong: REFUSE sebelum gerbang" "$(tail -1 "$K/out")"
# 8: kontrol alat -- penanda BENAR tanpa CEK_SAJA melewati pemeriksaan awal dan MENCAPAI gerbang (stub, rc 6)
jalan "routers/_uji_penanda_window.py" "$T" 0
[ "$(cat "$K/rc")" = 6 ] && [ -f "$K/dipanggil.txt" ]; cek $? "kontrol: penanda benar -> sampai gerbang (stub dipanggil, REFUSE gate rc=6)" "rc=$(cat "$K/rc")"
[ "$(git -C "$K/main" rev-parse HEAD)" = "$(git -C "$K/main" rev-parse deploy/master)" ]; cek $? "kotak pasir: main tiruan tak bergeser"
echo "RINGKAS gagal=$gagal"
[ $gagal -eq 0 ]
