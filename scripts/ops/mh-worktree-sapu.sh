#!/usr/bin/env bash
# mh-worktree-sapu.sh — sapu worktree yang sudah selesai, dengan pagar.
#
# Menghapus HANYA worktree yang memenuhi KETIGA syarat:
#   1. sudah ter-merge ke master   (nol commit di depan master)
#   2. sudah ada di remote deploy  (nol commit belum tercadang)
#   3. bersih                      (nol berkas termodifikasi/untracked)
# Yang tidak memenuhi DILEWATI, dengan alasan tercetak.
#
# Pemakaian:
#   ./mh-worktree-sapu.sh          # laporan saja, TIDAK menghapus (default aman)
#   ./mh-worktree-sapu.sh --hapus  # benar-benar menghapus
#
# Daftar kekecualian: worktree yang sengaja dipertahankan meski memenuhi syarat.
set -uo pipefail

UTAMA=/root/milkyhoop-dev
KECUALI="mh-autofulfill"     # harness AUTO-FULFILL: sengaja belum di-gate, jangan disapu

HAPUS=0
[ "${1:-}" = "--hapus" ] && HAPUS=1

[ -d "$UTAMA/.git" ] || { echo "FATAL: $UTAMA bukan repo git"; exit 1; }
cd "$UTAMA" || exit 1

# Segarkan pengetahuan tentang remote supaya syarat 2 tidak salah menuduh.
git fetch deploy --quiet 2>/dev/null || echo "PERINGATAN: fetch deploy gagal — syarat 'tercadang' memakai data lama"

[ "$HAPUS" = 1 ] && echo "MODE: HAPUS" || echo "MODE: laporan saja (pakai --hapus untuk benar-benar menghapus)"
echo
printf "%-24s %-34s %s\n" WORKTREE BRANCH KEPUTUSAN
printf "%.0s-" {1..96}; echo

n_sapu=0; n_lewat=0; n_gagal=0

for w in /root/mh-*; do
  [ -d "$w" ] || continue
  n=$(basename "$w")

  # Kekecualian eksplisit
  case " $KECUALI " in *" $n "*)
    printf "%-24s %-34s %s\n" "$n" "-" "LEWAT — dikecualikan"; n_lewat=$((n_lewat+1)); continue;; esac

  # Direktori yang bukan worktree sama sekali
  if [ ! -e "$w/.git" ]; then
    if [ -z "$(ls -A "$w" 2>/dev/null)" ]; then
      printf "%-24s %-34s %s\n" "$n" "-" "SAPU — direktori kosong, bukan worktree"
      [ "$HAPUS" = 1 ] && rmdir "$w" && n_sapu=$((n_sapu+1))
    else
      printf "%-24s %-34s %s\n" "$n" "-" "LEWAT — bukan worktree tapi TIDAK kosong"; n_lewat=$((n_lewat+1))
    fi
    continue
  fi

  b=$(git -C "$w" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ -z "$b" ] && { printf "%-24s %-34s %s\n" "$n" "?" "LEWAT — tak bisa membaca branch"; n_lewat=$((n_lewat+1)); continue; }

  ahead=$(git -C "$w" rev-list --count master..HEAD 2>/dev/null)
  unpushed=$(git -C "$w" rev-list --count HEAD --not --remotes=deploy 2>/dev/null)
  dirty=$(git -C "$w" status --porcelain 2>/dev/null | wc -l | tr -d ' ')

  alasan=""
  [ "$ahead"    != "0" ] && alasan="$alasan belum-merge(+$ahead)"
  [ "$unpushed" != "0" ] && alasan="$alasan belum-tercadang($unpushed)"
  [ "$dirty"    != "0" ] && alasan="$alasan kotor($dirty)"

  if [ -n "$alasan" ]; then
    printf "%-24s %-34s %s\n" "$n" "$b" "LEWAT —$alasan"; n_lewat=$((n_lewat+1)); continue
  fi

  printf "%-24s %-34s %s\n" "$n" "$b" "SAPU — ter-merge, tercadang, bersih"
  if [ "$HAPUS" = 1 ]; then
    if git worktree remove "$w" 2>/dev/null; then n_sapu=$((n_sapu+1))
    else echo "    GAGAL menghapus $n"; n_gagal=$((n_gagal+1)); fi
  else
    n_sapu=$((n_sapu+1))
  fi
done

[ "$HAPUS" = 1 ] && git worktree prune

echo
if [ "$HAPUS" = 1 ]; then echo "RINGKAS: $n_sapu disapu · $n_lewat dilewati · $n_gagal gagal"
else echo "RINGKAS: $n_sapu memenuhi syarat · $n_lewat dilewati · (belum ada yang dihapus)"; fi
echo "Sisa worktree: $(git worktree list | wc -l | tr -d ' ')"
