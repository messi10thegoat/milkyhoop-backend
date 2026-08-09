#!/bin/bash
# =============================================================================
# precondition_gate.sh — C2: GERBANG PRASYARAT, dijalankan SESUDAH step_-1 dan
# SEBELUM step_0, oleh KEDUA skenario.
#
# KENAPA ADA — dan kenapa ini bukan sekadar kenyamanan:
#   Preflight `restore_preharness` sudah MENCEGAH kontaminasi. Yang belum ada
#   adalah kemampuan MEMBACA kegagalannya. Pada 2026-08-09 sebuah gejala
#   ("bank 21.500.000 padahal harus 16.500.000") dibaca sebagai kontaminasi
#   antar-skenario, dan memicu perencanaan perbaikan untuk masalah yang tak
#   pernah ada — karena assert yang gagal tidak menyebut SEBAB, hanya angka.
#   Gerbang ini menyebut sebabnya dengan satu kata: KONTAMINASI.
#
#   Nilainya bukan mencegah kerusakan. Nilainya mencegah PEKERJAAN SIA-SIA.
#
# YANG DIPERIKSA — keadaan sesudah provisioning, sebelum transaksi pertama:
#   1. journal_entries = 1  (HANYA saldo awal bank; nol transaksi)
#   2. saldo bank = 20.000.000 (persis opening, belum ada arus kas)
#   3. tenant_config sesuai POLICY yang diminta pemanggil
#
# Assert di sini sengaja ABSOLUT, bukan delta. Assert delta LULUS di atas DB
# terkontaminasi — ia tahan-kontaminasi, dan itu justru cacatnya (Law 33
# mekanisme 7). Absolut yang tampak rapuh adalah satu-satunya detektor.
#
# Env: POLICY (default 'delivery'; 'none' = tenant_config WAJIB tanpa baris)
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/verdict.sh"
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }
POLICY=${POLICY:-delivery}
E_OPENBAL=${E_OPENBAL:-20000000}

echo "===== C2 — GERBANG PRASYARAT (sesudah step_-1, sebelum transaksi pertama) ====="
echo "  tenant=$TEN  POLICY diminta=$POLICY"

KOTOR=0
sebut(){ # $1=apa $2=aktual $3=diharapkan
  echo "  !!! KONTAMINASI — $1"
  echo "        aktual     : $2"
  echo "        diharapkan : $3"
  KOTOR=$((KOTOR+1))
}

# --- 1. hanya jurnal saldo awal -------------------------------------------
JE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
ST=$(PSQL "SELECT COALESCE(string_agg(DISTINCT source_type,',' ORDER BY source_type),'(nol)') FROM journal_entries WHERE tenant_id='$TEN';")
echo "  journal_entries=$JE  source_type=$ST"
if [ "${JE:-x}" != "1" ] || [ "$ST" != "OPENING" ]; then
  sebut "jurnal tenant bukan hanya saldo awal" "count=$JE source_type=$ST" "count=1 source_type=OPENING"
fi

# --- 2. bank persis opening ------------------------------------------------
BANK=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND jl.account_id='$BANK_COA';")
echo "  saldo bank (journal-derived)=$BANK"
[ "${BANK:-x}" = "$E_OPENBAL" ] || sebut "saldo bank sudah bergerak sebelum langkah pertama" "$BANK" "$E_OPENBAL"

# --- 3. policy sesuai yang diminta ----------------------------------------
POL=$(PSQL "SELECT COALESCE((SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id='$TEN'),'<no-row>');")
echo "  tenant_config policy=$POL"
if [ "$POLICY" = "none" ]; then
  [ "$POL" = "<no-row>" ] || sebut "tenant_config BOCOR dari run lain — skenario ini menuntut ketiadaan baris" "$POL" "<no-row>"
else
  [ "$POL" = "$POLICY" ] || sebut "policy bukan yang diminta" "$POL" "$POLICY"
fi

# --- verdict ----------------------------------------------------------------
if [ "$KOTOR" -ne 0 ]; then
  echo
  echo "  ============================================================"
  echo "   BERHENTI — DB TERKONTAMINASI ($KOTOR prasyarat menyimpang)."
  echo "   Ini BUKAN regresi produk dan BUKAN assert yang salah."
  echo "   Keadaan awal sudah salah sebelum langkah pertama dijalankan,"
  echo "   jadi setiap kegagalan sesudah ini TIDAK SAH sebagai bukti."
  echo "   Sebab yang paling mungkin: preflight restore dilewati"
  echo "   (SKIP_PREFLIGHT=1), atau step_-1 memakai tenant sisa run lain."
  echo "   Jalankan ulang dari nol; jangan mendiagnosa kegagalan di bawah."
  echo "  ============================================================"
  exit 1
fi
_pass "prasyarat bersih: 1 jurnal OPENING, bank $E_OPENBAL, policy=$POL"
finish
