#!/bin/bash
# Kontrol merah scripts/ops/jendela.sh di kotak pasir (repo git + skrip palsu; tanpa server, tanpa DB). rc=0 hanya bila semua skenario sesuai.
J="$(cd "$(dirname "$0")" && pwd)/jendela.sh"; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; gagal=0
cd "$T" || exit 2
git init -q main && git -C main -c user.email=a@b -c user.name=x commit -q --allow-empty -m m1
git clone -q main wt 2>/dev/null; git -C wt checkout -q -b cab; git -C wt -c user.email=a@b -c user.name=x commit -q --allow-empty -m w1
SHA=$(git -C wt rev-parse HEAD); MS=$(git -C main rev-parse HEAD)
printf '#!/bin/bash\necho BEBAS\n' > bebas.sh; printf '#!/bin/bash\necho "SIBUK"\n' > sibuk.sh
printf '#!/bin/bash\necho "$@" >> %s/mig.calls; exit ${FAKE_MIG_RC:-0}\n' "$T" > mig.sh
printf '#!/bin/bash\necho "$@" >> %s/wi.calls; exit 0\n' "$T" > wi.sh; chmod +x *.sh
printf 'x\nrc-penuh-rantai=0\nSELESAI\n' > ok.log; printf 'x\nrc-penuh-rantai=1\nSELESAI\n' > merah.log
jalan() {  # $1 label, $2 harapan (teks keluaran), $3 mig dipanggil? (0/1), $4 wi dipanggil? (0/1), sisanya = env tambahan
  local lab=$1 hrp=$2 em=$3 ew=$4; shift 4; rm -f mig.calls wi.calls
  local out; out=$(env LOG=$T/ok.log SHA=$SHA BR=cab WT=$T/wt MASTER_SHA=$MS NAMA=uji PENANDA_BERKAS=a PENANDA=b MH_MAIN=$T/main APPLY_MIG=$T/mig.sh WINDOW_ITEM=$T/wi.sh CEK_SIBUK=$T/bebas.sh MH_LOG_MAKS=2 MH_LOG_JEDA=1 MH_BEBAS_PUTARAN=2 MH_JEDA=1 JAM_FIX=1200 MIGRASI=V999__x.sql MIG_MARKER_BERKAS=m MIG_MARKER_TEKS=t "$@" bash "$J" 2>&1)
  local m=0 w=0; [ -f mig.calls ] && m=1; [ -f wi.calls ] && w=1
  if echo "$out" | grep -q "$hrp" && echo "$out" | grep -q "SEMUA SELESAI" && [ $m = "$em" ] && [ $w = "$ew" ]; then echo "PASS $lab"; else echo "FAIL $lab (mig=$m wi=$w) :: $(echo "$out" | tail -3 | tr '\n' '|')"; gagal=1; fi
}
jalan "k0 semua benar: migrasi lalu window_item" "uji rc=0" 1 1
jalan "k1 vonis != 0 -> lewat" "TIDAK lulus" 0 0 LOG=$T/merah.log
jalan "k2 HEAD beda -> lewat" "HEAD worktree berubah" 0 0 SHA=deadbeefdeadbeef
jalan "k3 master bergerak -> lewat" "master bergerak" 0 0 MASTER_SHA=deadbeefdeadbeef
git -C wt checkout -q --detach; jalan "k4 detached -> lewat" "bukan cabang lokal" 0 0; git -C wt checkout -q cab
echo kotor > wt/f; jalan "k5 worktree kotor -> lewat" "worktree kotor" 0 0; rm -f wt/f
jalan "k6 migrasi REFUSE -> window_item tak jalan" "migrasi gagal" 1 0 FAKE_MIG_RC=3
jalan "k7 server SIBUK -> TUNDA" "rc=TUNDA" 0 0 CEK_SIBUK=$T/sibuk.sh
jalan "k8 jeda apt 02:45-03:30 -> TUNDA" "rc=TUNDA" 0 0 JAM_FIX=0300
jalan "k9 tanpa MIGRASI: window_item saja" "uji rc=0" 0 1 MIGRASI=
jalan "k10 log tak selesai -> lewat" "log rantai tak selesai" 0 0 LOG=$T/tidak-ada.log
jalan "k11 migrasi dipanggil dengan --require-marker + penanda kode live" "uji rc=0" 1 1
grep -q -- "--require-marker m t" mig.calls && echo "PASS k11b argumen migrasi" || { echo "FAIL k11b argumen migrasi :: $(cat mig.calls)"; gagal=1; }
echo "rc=$gagal"; exit $gagal
