#!/bin/bash
# =============================================================================
# run_all.sh — DP-flow SINGLE-SHOT regression runner (FASE 5 deliverable).
# Runs the full cash-to-cash DP flow end to end, ONE shot, no intervention:
#   step -1 -> 0 -> 0b -> 1 -> 2 -> [3 SKIP] -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> closing_invariant
# drift_check.sql runs AFTER every step. Stops at the FIRST failure with a clear message
# (which step, the offending lines, exit code). Records per-step + total duration.
#
# PRASYARAT — DIJALANKAN OTOMATIS oleh skrip ini (blok PREFLIGHT di bawah):
#   1. restore_preharness.sh  — milkydb kembali pristine (Tenant=0, journal=0)
#   2. migrate.sh apply       — jalankan RANTAI MIGRASI di atas snapshot
#   3. migrate.sh verify      — gate keras: nol V*.sql on-disk yang belum tracked
#   4. run flow -> on failure: perbaiki, jalankan ulang dari nol (JANGAN resume mid-flow)
#
# KENAPA restore->apply, BUKAN "bikin snapshot baru ber-skema master":
#   Snapshot ber-skema master menghapus properti terpenting harness ini — ia
#   MENJALANKAN RANTAI MIGRASI setiap run. Snapshot baru hanya menguji skema
#   AKHIR tanpa pernah menguji JALAN menuju ke sana; migrasi yang rusak/tak
#   idempoten tak akan pernah ketahuan. Godaan "bikin snapshot baru biar tak
#   perlu apply" HARUS DITOLAK.
#
# SEJARAH (2026-08-06): header lama menjanjikan langkah 1-2 sebagai "run BEFORE
# this script" tetapi skrip ini NOL memanggilnya, dan tak ada gate yang
# memeriksanya. Akibatnya (a) menjalankan run_all di atas DB kotor menghasilkan
# kegagalan yang tampak seperti regresi produk — nyaris salah lapor regresi
# V221; dan (b) karena restore_preharness memundurkan skema ke tanggal snapshot
# (26 Juli, s/d V220), migrasi yang lebih baru DIAM-DIAM hilang sehingga harness
# menguji skema LAMA sambil melaporkan hijau. Law 33 instance kelima:
# mekanismenya bukan gate yang bisu, melainkan PRASYARAT yang tak dijalankan —
# sehingga hasil hijau/merahnya tak sah sama sekali.
#
# FAILURE DETECTION — rc-PRIMARY (owner directive; string matching alone is a silent-fallback
# risk). Every child now EXITS NON-ZERO on failure: step scripts source verdict.sh and call
# finish() (exit 1 if any assertion failed); drift_check.sql and closing_invariant.sql end in a
# division-by-zero rc-gate under ON_ERROR_STOP. So each step AND its drift are gated on exit
# code != 0 FIRST. The token scan (FAILRE below) is only a SAFETY BELT for any legacy printed
# verdict that a future edit forgets to route through finish(). A bare `set -e` is still not
# enough on its own (the child pipes through tee), so we capture PIPESTATUS and gate explicitly.
# =============================================================================
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="$DIR/state.env"
LOGDIR="$DIR/.run_all_logs"
# A genuine verdict token: "FAIL" followed by EOL / whitespace / "(" (table cells, inline
# verdicts, "FAIL — reason", "FAIL (reason)"), plus "!!!" and uppercase ABORT. Deliberately
# does NOT match "FAIL)" — that only occurs in DESCRIPTIVE echoes like "(expect ... FAIL)".
FAILRE='FAIL([[:space:]]|$|\()|!!!|ABORT'
CONTAINER=${CONTAINER:-milkyhoop-dev-postgres-1}
DB=${DB:-milkydb}

rm -rf "$LOGDIR"; mkdir -p "$LOGDIR"
rm -f "$STATE"          # RISK: no stale IDs from a previous run leak in.

RUN_T0=$(date +%s)
echo "======================================================================"
echo " DP-FLOW SINGLE-SHOT RUN  —  $(date -u +%FT%TZ)"
echo "======================================================================"

# ============================ PREFLIGHT ============================
# Prasyarat yang dulu hanya dijanjikan header. SKIP_PREFLIGHT=1 hanya untuk
# rerun cepat di DB yang SUDAH disiapkan tangan — bukan untuk CI/verdict.
REPO_ROOT="$(cd "$DIR/../../.." && pwd)"
if [ "${SKIP_PREFLIGHT:-0}" = "1" ]; then
  echo "!!! PREFLIGHT DILEWATI (SKIP_PREFLIGHT=1) — hasil run ini TIDAK SAH sebagai verdict."
else
  echo "--- PREFLIGHT 1/3: restore preharness ---"
  bash "$DIR/restore_preharness.sh" || { echo "!!! PREFLIGHT GAGAL: restore_preharness"; exit 9; }

  if [ "${PREFLIGHT_SKIP_APPLY:-0}" = "1" ]; then
    # HOOK UJI-MERAH (Law 33) — mensimulasikan bug nyata: restore memundurkan
    # skema lalu apply tak dijalankan. Gate 3/3 di bawah HARUS menangkapnya.
    echo "!!! PREFLIGHT 2/3 DILEWATI SENGAJA (PREFLIGHT_SKIP_APPLY=1) — uji-merah"
  else
    echo "--- PREFLIGHT 2/3: migrate.sh apply (jalankan rantai migrasi) ---"
    # MIGDIR dipaksa ke tree yang SEDANG DIUJI. migrate.sh sudah relatif
    # terhadap dirinya sendiri sejak 2026-08-07, tapi ini sabuk kedua yang
    # membuat niatnya terbaca di tempat kejadian.
    ( cd "$REPO_ROOT" && MIGDIR="$REPO_ROOT/backend/migrations" \
        bash scripts/fresh-install/migrate.sh apply ) \
      || { echo "!!! PREFLIGHT GAGAL: migrate.sh apply"; exit 9; }
  fi

  echo "--- PREFLIGHT 3/3: gate skema (nol V*.sql on-disk yang belum tracked) ---"
  # Assert memakai migrate.sh verify, BUKAN "count(tracked) == count(file)":
  # schema_migrations memuat entri pembukuan non-VNNN (GAP_PATCH, STEP0_STUB),
  # jadi kesamaan jumlah TIDAK PERNAH benar (213 file vs 215 tracked) dan
  # assert bentuk itu akan gagal-palsu selamanya.
  if ! ( cd "$REPO_ROOT" && MIGDIR="$REPO_ROOT/backend/migrations" \
         bash scripts/fresh-install/migrate.sh verify ); then
    echo "!!! PREFLIGHT GAGAL: skema tertinggal dari MIGDIR."
    echo "    Ini gejala 'restore memundurkan skema tanpa apply'."
    echo "    Hasil run apa pun setelah ini TIDAK SAH — dihentikan."
    exit 9
  fi
  echo "--- PREFLIGHT OK: DB pristine + skema = MIGDIR ---"
fi
# ========================== /PREFLIGHT =============================

drift(){ # authoritative drift after each step (per spec). Needs TEN from state.env.
  [ -f "$STATE" ] || { echo "  (no state.env yet — drift skipped for this step)"; return 0; }
  # shellcheck disable=SC1090
  local TEN; TEN=$(grep -E '^export TEN=' "$STATE" | head -1 | sed -E 's/^export TEN="?([^"]*)"?/\1/')
  [ -n "$TEN" ] || { echo "  (TEN unset — drift skipped)"; return 0; }
  docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
}

gate(){ # $1=label  $2=logfile  $3=child-exit-code   -> exit 1 on first failure
  local label=$1 log=$2 rc=$3
  if [ "$rc" -ne 0 ]; then
    echo; echo "!!!!! STOP — step $label exited non-zero (rc=$rc). First failure. !!!!!"
    tail -20 "$log"; exit 1
  fi
  if grep -Eq "$FAILRE" "$log"; then
    echo; echo "!!!!! STOP — step $label reported a FAILURE. First failure. !!!!!"
    echo "----- offending lines: -----"
    grep -nE "$FAILRE" "$log" | head -20
    exit 1
  fi
}

step(){ # $1=label  $2=script
  local label=$1 script=$2
  local log="$LOGDIR/step_${label}.log"
  echo; echo "############################## STEP $label ($script) ##############################"
  local t0; t0=$(date +%s)
  bash "$DIR/$script" 2>&1 | tee "$log"; local rc=${PIPESTATUS[0]}
  echo; echo "-------- [run_all] drift_check after step $label --------"
  drift 2>&1 | tee "$LOGDIR/drift_${label}.log"; local drc=${PIPESTATUS[0]}
  local t1; t1=$(date +%s); local dur=$((t1-t0))
  printf '%-4s  %3ss  (child exit %s)\n' "$label" "$dur" "$rc" >> "$LOGDIR/timing.txt"
  echo "-------- step $label finished in ${dur}s (child exit $rc) --------"
  gate "$label" "$log" "$rc"
  gate "$label-drift" "$LOGDIR/drift_${label}.log" "$drc"
}

step  "-1" step_-1_provision.sh
step  "0"  step_0_buy.sh
step  "0b" step_0b_pay.sh
step  "1"  step_1_quote.sh
step  "2"  step_2_convert.sh
echo; echo "############################## STEP 3 ##############################"
echo "STEP 3 — TAGIH DP via PENAWARAN (quote) — NOT a separate document (owner correction 2026-07-27)."
echo "  In UMKM practice the quote IS the DP billing instrument: it carries dp_amount/dp_percent +"
echo "  payment bank/account/holder (created in step 1; surfaced via GET + PDF, gated by C2/C3)."
echo "  There is no separate 'faktur DP' endpoint BY DESIGN — this is not a gap."
step  "4"  step_4_dp.sh
step  "5"  step_5_invoice.sh
step  "6"  step_6_apply.sh
step  "7"  step_7_fulfill.sh
step  "8"  step_8_settle.sh
step  "9"  step_9_close.sh

echo; echo "############################## FASE 6 — CLOSING INVARIANT ##############################"
# shellcheck disable=SC1090
source "$STATE"
CLOG="$LOGDIR/closing.log"
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" \
  -v ten="'$TEN'" -v bankcoa="'$BANK_COA'" -v openbal=20000000 \
  -f - < "$DIR/closing_invariant.sql" 2>&1 | tee "$CLOG"
CRC=${PIPESTATUS[0]}
gate "closing" "$CLOG" "$CRC"

RUN_T1=$(date +%s); TOTAL=$((RUN_T1-RUN_T0))
echo; echo "======================================================================"
echo " ✅ SINGLE-SHOT RUN COMPLETE — ALL STEPS PASS, closing invariant clean."
echo "======================================================================"
echo "Per-step timing:"; cat "$LOGDIR/timing.txt"
echo "TOTAL: ${TOTAL}s"
