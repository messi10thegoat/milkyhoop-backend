#!/bin/bash
# =============================================================================
# run_all.sh — SKENARIO #2 (AUTO-FULFILL) single-shot runner.
#
#   step -1 (POLICY=none) -> 0 -> 0b -> 1 (posting=3 jurnal) -> [2 DILEWATI] -> 3
#   -> closing_invariant -> pagar WAC-0 -> cleanup (assert keadaan akhir)
#
# KENAPA SKENARIO INI ADA: seluruh flow DP terbukti runtime, TAPI ia memakai
# delivery mode — setelan yang NOL jalur API, hanya bisa dicapai harness dengan
# menulis langsung ke DB. Yang 100% tenant nyata dapatkan adalah AUTO-FULFILL,
# dan itu belum pernah dieksekusi sekali pun.
#
# REUSE, BUKAN SALIN. Dipakai apa adanya dari ../dp_flow:
#   restore_preharness.sh · step_-1_provision.sh · step_0_buy.sh · step_0b_pay.sh
#   verdict.sh · dates.env · drift_check.sql · closing_invariant.sql
# closing_invariant.sql cocok VERBATIM: keadaan akhir kedua skenario identik
# (AR/AP/persediaan/deposit/deferred = 0, penjualan 5jt, HPP 3,5jt, bank delta
# +1,5jt) — hanya JALANNYA yang berbeda. Itu justru yang membuatnya bermakna:
# invariant yang sama dicapai lewat dua rute berbeda.
#
# SATU-SATUNYA PERUBAHAN pada skrip bersama: step_-1_provision.sh kini menerima
# POLICY (default 'delivery' = perilaku lama, tak berubah). POLICY=none membuatnya
# TIDAK menulis tenant_config sama sekali — mereproduksi tenant nyata, yang tak
# punya baris itu. Menyalin skrip 11 KB demi satu variabel akan melahirkan
# salinan kedua yang membusuk terpisah.
#
# DETEKSI KEGAGALAN: rc-PRIMARY (sama seperti dp_flow) — tiap child exit != 0
# lewat verdict.sh finish(); token scan hanya sabuk kedua.
# =============================================================================
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DP="$DIR/../dp_flow"
STATE="$DP/state.env"
LOGDIR="$DIR/.run_all_logs"
FAILRE='FAIL([[:space:]]|$|\()|!!!|ABORT'
CONTAINER=${CONTAINER:-milkyhoop-dev-postgres-1}
DB=${DB:-milkydb}

rm -rf "$LOGDIR"; mkdir -p "$LOGDIR"
rm -f "$STATE"          # nol ID basi dari run sebelumnya

RUN_T0=$(date +%s)
echo "======================================================================"
echo " AUTO-FULFILL SINGLE-SHOT RUN  —  $(date -u +%FT%TZ)"
echo "======================================================================"

REPO_ROOT="$(cd "$DIR/../../.." && pwd)"
if [ "${SKIP_PREFLIGHT:-0}" = "1" ]; then
  echo "!!! PREFLIGHT DILEWATI (SKIP_PREFLIGHT=1) — hasil run ini TIDAK SAH sebagai verdict."
else
  echo "--- PREFLIGHT 1/3: restore preharness ---"
  bash "$DP/restore_preharness.sh" || { echo "!!! PREFLIGHT GAGAL: restore_preharness"; exit 9; }
  echo "--- PREFLIGHT 2/3: migrate.sh apply ---"
  ( cd "$REPO_ROOT" && MIGDIR="$REPO_ROOT/backend/migrations" bash scripts/fresh-install/migrate.sh apply ) \
    || { echo "!!! PREFLIGHT GAGAL: migrate.sh apply"; exit 9; }
  echo "--- PREFLIGHT 3/3: gate skema ---"
  ( cd "$REPO_ROOT" && MIGDIR="$REPO_ROOT/backend/migrations" bash scripts/fresh-install/migrate.sh verify ) \
    || { echo "!!! PREFLIGHT GAGAL: skema tertinggal dari MIGDIR — hasil run TIDAK SAH."; exit 9; }
  echo "--- PREFLIGHT OK ---"
fi

drift(){
  [ -f "$STATE" ] || { echo "  (belum ada state.env — drift dilewati)"; return 0; }
  local TEN; TEN=$(grep -E '^export TEN=' "$STATE" | head -1 | sed -E 's/^export TEN="?([^"]*)"?/\1/')
  [ -n "$TEN" ] || { echo "  (TEN kosong — drift dilewati)"; return 0; }
  docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DP/drift_check.sql"
}

gate(){
  local label=$1 log=$2 rc=$3
  if [ "$rc" -ne 0 ]; then
    echo; echo "!!!!! STOP — step $label keluar non-zero (rc=$rc). Kegagalan pertama. !!!!!"
    tail -25 "$log"; exit 1
  fi
  if grep -Eq "$FAILRE" "$log"; then
    echo; echo "!!!!! STOP — step $label melaporkan KEGAGALAN. Kegagalan pertama. !!!!!"
    grep -nE "$FAILRE" "$log" | head -20
    exit 1
  fi
}

step(){ # $1=label  $2=path-skrip  $3..=env prefix
  local label=$1 script=$2; shift 2
  local log="$LOGDIR/step_${label}.log"
  echo; echo "############################## STEP $label ($(basename "$script")) ##############################"
  local t0; t0=$(date +%s)
  env "$@" bash "$script" 2>&1 | tee "$log"; local rc=${PIPESTATUS[0]}
  echo; echo "-------- drift_check sesudah step $label --------"
  drift 2>&1 | tee "$LOGDIR/drift_${label}.log"; local drc=${PIPESTATUS[0]}
  local t1; t1=$(date +%s)
  printf '%-6s  %3ss  (child exit %s)\n' "$label" "$((t1-t0))" "$rc" >> "$LOGDIR/timing.txt"
  echo "-------- step $label selesai dalam $((t1-t0))s (child exit $rc) --------"
  gate "$label" "$log" "$rc"
  gate "$label-drift" "$LOGDIR/drift_${label}.log" "$drc"
}

# POLICY=none: JANGAN tulis tenant_config. Inilah satu-satunya perbedaan
# konfigurasi antara skenario #1 dan #2 — dijaga sebagai variabel tunggal.
step "-1" "$DP/step_-1_provision.sh" POLICY=none
step "0"  "$DP/step_0_buy.sh"
step "0b" "$DP/step_0b_pay.sh"
step "1"  "$DIR/step_1_invoice_autopost.sh"

echo; echo "############################## STEP 2 — PENGIRIMAN ##############################"
echo "DILEWATI DENGAN SENGAJA. Faktur sudah fulfilled sejak POSTING (auto-fulfill)."
echo "Ini bukan langkah yang hilang; ketiadaannya JUSTRU yang diuji — step 1 ASSERT G"
echo "membuktikan GET /fulfillments melaporkan nol sisa yang bisa dikirim."

step "3"  "$DIR/step_3_receipt.sh"

echo; echo "############################## CLOSING INVARIANT ##############################"
# shellcheck disable=SC1090
source "$STATE"
CLOG="$LOGDIR/closing.log"
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" \
  -v ten="'$TEN'" -v bankcoa="'$BANK_COA'" -v openbal=20000000 \
  -f - < "$DP/closing_invariant.sql" 2>&1 | tee "$CLOG"
gate "closing" "$CLOG" "${PIPESTATUS[0]}"

# Pagar sisi-sehat SESUDAH invariant: ia sengaja meninggalkan AR 600.000, jadi
# harus dinilai setelah keadaan bersih skenario utama diverifikasi.
step "WAC0" "$DIR/step_R_wac0_guard.sh"
step "clean" "$DIR/cleanup.sh"

RUN_T1=$(date +%s)
echo; echo "======================================================================"
echo " ✅ AUTO-FULFILL RUN SELESAI — SEMUA STEP LULUS, invariant bersih."
echo "======================================================================"
echo "Timing per step:"; cat "$LOGDIR/timing.txt"
echo "TOTAL: $((RUN_T1-RUN_T0))s"
