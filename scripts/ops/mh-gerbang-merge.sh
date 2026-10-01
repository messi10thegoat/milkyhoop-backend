#!/usr/bin/env bash
# mh-gerbang-merge.sh — GERBANG MERGE BE: cabang boleh masuk master HANYA bila suite unit
# HIJAU pada hasil merge-nya (dasar + cabang), diukur di pohon sekali-pakai.
#
# KENAPA BERKAS INI ADA
# 23 Sep 2026: rantai perintah sesi BACKEND mendorong cabang (3g) SEBELUM membaca baris
# ringkasan suite -- suite-nya 4 merah. Saudara kelas "grep sukses saat menemukan 'failed'"
# (22 Sep): penutup rantai menelan status di hulu. Gerbang merge tidak boleh berupa kebiasaan
# di kepala sesi; ia harus skrip yang menolak.
#
# Pemakaian:  mh-gerbang-merge.sh <cabang|sha> [--dasar <ref>] [--izinkan-ubah-gerbang]
#   --dasar  default deploy/master (sesudah fetch).
# Keluar:  0 = LULUS (boleh di-merge)   1 = DITOLAK   2 = galat alat (JANGAN dianggap lulus)
#
# Yang DITOLAK:
#   * merge bentrok;
#   * pytest rc != 0, baris ringkasan tak ada, ada failed/error, 0 passed (mis. tak ada tes
#     terkumpul), atau passed < LANTAI;
#   * cabang mengubah berkas GERBANG (runner, pytest-unit.ini, conftest unit, skrip ini,
#     berkas lantai) -- kecuali --izinkan-ubah-gerbang (dicetak keras di laporan).
# SENGAJA TIDAK menerima argumen pytest: `-k`/`--deselect` akan membuat merah hilang dari
#   pandangan, bukan dari kode.
# Runner dan LANTAI dibaca dari DASAR, bukan dari cabang: cabang tak bisa melonggarkan
#   gerbangnya sendiri. Menurunkan lantai = commit tersendiri ke master, terlihat.
set -uo pipefail

REPO=${MH_REPO:-/root/milkyhoop-dev}
CABANG=""; DASAR=""; IZIN_GERBANG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dasar) DASAR=${2:-}; shift 2 ;;
    --izinkan-ubah-gerbang) IZIN_GERBANG=1; shift ;;
    -*) echo "galat: opsi tak dikenal $1 (argumen pytest sengaja TIDAK diterima)"; exit 2 ;;
    *) [ -z "$CABANG" ] && CABANG=$1 || { echo "galat: argumen berlebih $1"; exit 2; }; shift ;;
  esac
done
[ -n "$CABANG" ] || { sed -n '12,16p' "$0"; exit 2; }

git -C "$REPO" fetch -q deploy || { echo "galat: fetch deploy gagal"; exit 2; }
DASAR=${DASAR:-deploy/master}
DASAR_SHA=$(git -C "$REPO" rev-parse --verify -q "$DASAR^{commit}") || { echo "galat: dasar '$DASAR' tak dikenal"; exit 2; }
CABANG_SHA=$(git -C "$REPO" rev-parse --verify -q "$CABANG^{commit}") || { echo "galat: cabang '$CABANG' tak dikenal"; exit 2; }

TOLAK() { echo "DITOLAK: $*"; echo "  cabang $CABANG (${CABANG_SHA:0:8}) di atas $DASAR (${DASAR_SHA:0:8})"; exit 1; }

# 1. berkas gerbang yang disentuh cabang
GERBANG_RE='^(scripts/jalankan_unit\.sh|backend/api_gateway/pytest-unit\.ini|backend/api_gateway/tests/unit/conftest\.py|scripts/ops/mh-gerbang-merge\.sh|scripts/ops/gerbang-merge\.lantai|scripts/gate_izin_rute\.py)$'
SENTUH=$(git -C "$REPO" diff --name-only "$(git -C "$REPO" merge-base "$DASAR_SHA" "$CABANG_SHA")" "$CABANG_SHA" | grep -E "$GERBANG_RE" || true)
if [ -n "$SENTUH" ]; then
  if [ $IZIN_GERBANG -eq 1 ]; then
    echo "PERHATIAN: cabang MENGUBAH berkas gerbang (diizinkan dengan --izinkan-ubah-gerbang):"; echo "$SENTUH" | sed 's/^/    /'
  else
    TOLAK "cabang mengubah berkas gerbang: $(echo $SENTUH)"
  fi
fi

# 2. lantai dari DASAR
LANTAI=$(git -C "$REPO" show "$DASAR_SHA:scripts/ops/gerbang-merge.lantai" 2>/dev/null | grep -oE '^[0-9]+' | head -1)
if [ -z "$LANTAI" ]; then
  LANTAI=$(git -C "$REPO" show "$CABANG_SHA:scripts/ops/gerbang-merge.lantai" 2>/dev/null | grep -oE '^[0-9]+' | head -1)
  [ -n "$LANTAI" ] || { echo "galat: berkas lantai tak ada di dasar maupun cabang"; exit 2; }
  echo "PERHATIAN: dasar belum punya lantai; memakai lantai CABANG ($LANTAI) -- hanya sah untuk merge pertama gerbang ini."
fi

# 3. pohon sekali-pakai = dasar + merge cabang
T=$(mktemp -d /tmp/mh-gerbang-XXXXXX); RUN=$(mktemp /tmp/mh-gerbang-runner-XXXXXX.sh)
BERSIH() { git -C "$REPO" worktree remove --force "$T" >/dev/null 2>&1; git -C "$REPO" worktree prune; rm -rf "$T" "$RUN"; }
trap BERSIH EXIT
rmdir "$T"
git -C "$REPO" worktree add -q --detach "$T" "$DASAR_SHA" || { echo "galat: worktree add gagal"; exit 2; }
if ! git -C "$T" -c user.email=gerbang@milkyhoop -c user.name=gerbang merge -q --no-ff --no-edit "$CABANG_SHA" >/dev/null 2>&1; then
  TOLAK "merge bentrok dengan dasar"
fi
git -C "$REPO" show "$DASAR_SHA:scripts/jalankan_unit.sh" > "$RUN" || { echo "galat: runner tak ada di dasar"; exit 2; }

# 3b. KESEGARAN inventaris rute izin (1 Okt 2026). test_pagar_rute_izin / test_izin_rute_pratinjau membaca
# inventaris_rute_tulis.txt yang DI-COMMIT; inventaris basi = penjaga buta tapi HIJAU (16 rute CW lolos 26-30 Sep
# -> ADMIN 403). gate_izin_rute.py membandingkan inventaris dgn app.routes pohon GABUNGAN. Skrip dari DASAR (cabang
# tak bisa melemahkannya), kontainer sekali-pakai :ro. Lulus HANYA bila rc 0 DAN baris "HIJAU: n/n".
IZG=$(mktemp /tmp/mh-gerbang-izin-XXXXXX.py)
git -C "$REPO" show "$DASAR_SHA:scripts/gate_izin_rute.py" > "$IZG" || { rm -f "$IZG"; echo "galat: scripts/gate_izin_rute.py tak ada di dasar"; exit 2; }
IZO=$(docker run --rm -i --network milkyhoop_dev_network --env-file "$REPO/.env" -v "$T/backend:/app/backend:ro" -w /app \
      milkyhoop-dev-api_gateway:latest python - < "$IZG" 2>&1); IZRC=$?; rm -f "$IZG"
IZB=$(echo "$IZO" | grep -E '^(HIJAU|MERAH): [0-9]+/[0-9]+$' | tail -1)
echo "izin-rute: rc=$IZRC | ${IZB:-<tak ada baris ringkasan>}"
if ! { [ "$IZRC" -eq 0 ] && echo "$IZB" | grep -qE '^HIJAU: ([0-9]+)/\1$'; }; then
  echo "$IZO" | grep -F '[X]' | sed 's/^/    /'
  TOLAK "kesegaran izin rute MERAH (inventaris_rute_tulis.txt basi / rute tulis tanpa pola) -- jalankan scripts/gate_izin_rute.py"
fi

# 4. suite
mkdir -p /root/logs; LOG=/root/logs/gerbang-merge-$(echo "$CABANG" | tr '/' '_')-$(date -u +%Y%m%dT%H%M%S).log
bash "$RUN" "$T" > "$LOG" 2>&1; RC=$?
BARIS=$(grep -E '^=+ .* in [0-9.]+s( \([0-9:]+\))? =+$' "$LOG" | tail -1)
angka() { echo "$BARIS" | grep -oE "[0-9]+ $1" | grep -oE '^[0-9]+' | head -1; }
LULUS=$(angka passed); GAGAL=$(angka failed); GALAT=$(angka 'errors?'); LEWAT=$(angka skipped)
LULUS=${LULUS:-0}; GAGAL=${GAGAL:-0}; GALAT=${GALAT:-0}
echo "suite: rc=$RC | ${BARIS:-<tak ada baris ringkasan>} | lantai $LANTAI | log $LOG"
[ -n "$BARIS" ] || TOLAK "baris ringkasan pytest tak ditemukan (rc=$RC)"
[ "$GAGAL" -eq 0 ] && [ "$GALAT" -eq 0 ] || { grep -E '^(FAILED|ERROR) ' "$LOG" | head -10 | sed 's/^/    /'; TOLAK "$GAGAL failed, $GALAT error"; }
[ "$RC" -eq 0 ] || TOLAK "pytest rc=$RC (5 = tak ada tes terkumpul)"
[ "$LULUS" -gt 0 ] || TOLAK "0 tes lulus"
[ "$LULUS" -ge "$LANTAI" ] || TOLAK "$LULUS passed < lantai $LANTAI (tes hilang dari koleksi?)"
echo "LULUS: $CABANG (${CABANG_SHA:0:8}) di atas $DASAR (${DASAR_SHA:0:8}) -- $LULUS passed, ${LEWAT:-0} skipped, lantai $LANTAI"
exit 0
