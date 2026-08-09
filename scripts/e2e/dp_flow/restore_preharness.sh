#!/bin/bash
# restore_preharness.sh — restore milkydb from the pristine preharness snapshot and
# verify it is pristine. RESTORE ONLY (no steps). ALLOW_CONNECTIONS-false to win the
# drop-vs-pool race; re-enables connections on any failure. Then asserts the pristine
# invariants the single-shot run depends on.
# DIUJI DUA ARAH: 2026-08-09   <- penanda WAJIB, seragam lintas jaring (Law 33 aturan 1)
#   SATU penanda per berkas — tanggalnya = uji dua-arah TERAKHIR yang lulus.
#   Diuji di DB KLON lewat DB= (live TIDAK disentuh):
#   (a) snapshot sah        -> PRISTINE OK, Tenant/User/journal = 0, migrasi 214   [08-08]
#   (b) snapshot dipotong   -> GAGAL KERAS di pemuatan (rc != 0), bukan "PRISTINE OK" [08-08]
#   (c) C1: tenant asing    -> MENOLAK, dan DB TETAP UTUH (nol DROP)               [08-09]
#   (d) C1: harness saja    -> jalan normal                                        [08-09]
#   (e) C1: I_KNOW=1 + asing-> jalan, dan MENCETAK daftar yang akan dihapus        [08-09]
#   (f) C1: skema rusak     -> MENOLAK (fail-closed), I_KNOW=1 TIDAK melewatinya   [08-09]
#   (g) C1: HARNESS_SLUG unset -> tetap MENOLAK (default berlaku)                  [08-09]
#   (h) C1: HARNESS_SLUG=""    -> tetap MENOLAK (:- mengganti string kosong)       [08-09]
#   Uji ULANG kalau snapshot, versi postgres, atau nama kontainer berubah.
set -uo pipefail
SNAP=${SNAP:-/root/milkydb_preharness_20260726_022045.sql.gz}
# DB dapat di-override HANYA untuk pengujian. Default = milkydb (perilaku lama,
# tak berubah). Tanpa ini skrip mustahil diuji tanpa menghancurkan live —
# dan yang mustahil diuji tak pernah diuji. (kelas: WT di-hardcode)
DB=${DB:-milkydb}
C=milkyhoop-dev-postgres-1
PG(){ docker exec -i "$C" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "$1"; }
Q(){ docker exec -i "$C" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }

echo "===== RESTORE $DB from $SNAP ====="

# ---------------------------------------------------------------------------
# C1 — GERBANG KESELAMATAN (2026-08-09). Pola dicontek PERSIS dari
# scripts/e2e/test_gateway.sh, bukan versi baru.
#
# KENAPA ADA: skrip ini men-DROP DATABASE seluruh pembukuan, dan sampai hari
# ini SATU-SATUNYA pemeriksaan tenant ada SESUDAH restore — itu verifikasi
# hasil, bukan izin bertindak. Padahal test_gateway.sh, yang cuma menyalakan
# container baca-saja, MENOLAK start bila ada tenant di luar slug harness.
# Alat yang jauh lebih destruktif punya guard yang jauh lebih lemah.
# Sesi lain (conversational) memakai milkydb yang SAMA: tiap run_all
# menghapus datanya tanpa peringatan dan tanpa jejak.
#
# JANGAN dilonggarkan dengan mengubah HARNESS_SLUG — itu mengakali guard,
# bukan melewatinya secara sadar. Untuk melewatinya, pakai I_KNOW=1, yang
# akan MENCETAK apa yang akan dihapus.
# ---------------------------------------------------------------------------
HARNESS_SLUG=${HARNESS_SLUG:-kaos-biru-konveksi}
# ${VAR:-default} mengganti saat UNSET **maupun KOSONG**, jadi HARNESS_SLUG=""
# tetap jatuh ke slug harness. Diuji, bukan diandaikan (kasus (g)/(h)).

# _G — pembaca khusus guard, FAIL-CLOSED. JANGAN ganti dengan Q().
# Q() mem-pipe psql ke `tr`, sehingga $? adalah exit TR (selalu 0) dan
# kegagalan psql lenyap. TERBUKTI 2026-08-09: `psql|tr` exit 0 sementara psql
# exit 1 -> `|| echo ERR` tak pernah menyala -> BAD='' -> ${BAD:-0}=0 ->
# guard LOLOS dan melanjutkan ke DROP DATABASE. Guard keselamatan tak boleh
# meminjam helper kenyamanan yang menelan galat.
# Tiga hal diperlakukan sebagai ERR: rc != 0, keluaran kosong, keluaran
# bukan angka. Ketiganya sama-sama berarti "aku tidak tahu" — dan
# "tidak tahu" harus berarti MENOLAK, bukan melanjutkan.
_G(){
  local _out _rc
  _out=$(docker exec -i "$C" psql -U postgres -d "$DB" -tA -c "$1" 2>/dev/null)
  _rc=$?
  _out=$(printf '%s' "$_out" | tr -d '[:space:]')
  if [ "$_rc" -ne 0 ] || [ -z "$_out" ] || ! printf '%s' "$_out" | grep -qE '^[0-9]+$'; then
    printf 'ERR'; return 0
  fi
  printf '%s' "$_out"
}
_DBEXISTS=$(docker exec -i "$C" psql -U postgres -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname='$DB'" 2>/dev/null | tr -d '[:space:]')
if [ "$_DBEXISTS" = "1" ]; then
  TN=$(_G "SELECT count(*) FROM \"Tenant\";")
  BAD=$(_G "SELECT count(*) FROM \"Tenant\" WHERE id <> '$HARNESS_SLUG';")
  # Kegagalan ALAT tak boleh menyamar jadi "nol tenant asing" — itu justru
  # bacaan yang paling meyakinkan dan paling salah (Law 33 mekanisme 7).
  if [ "$TN" = "ERR" ] || [ "$BAD" = "ERR" ]; then
    echo "!!! tak bisa membaca tabel \"Tenant\" di '$DB' (TN=$TN BAD=$BAD)."
    echo "    Ini kegagalan ALAT, bukan izin. Bisa berarti skema rusak separuh."
    echo "    ==> MENOLAK. I_KNOW=1 TIDAK melewati kasus ini: kalau kita tak bisa"
    echo "        membaca apa yang akan dihapus, kita tak bisa menyetujuinya."
    exit 1
  fi
  if [ "${BAD:-0}" -gt 0 ]; then
    # SATU blok, urutan keputusan eksplisit. Versi pertama menaruh cetakan
    # I_KNOW SESUDAH cabang penolakan yang ber-`exit 1`, sehingga escape hatch
    # itu KODE MATI — tak pernah tercapai. Ditemukan uji dua arah kasus (e),
    # bukan oleh membaca ulang. Guard yang jalurnya tak dijatuhi beban punya
    # cabang mati tanpa ada yang tahu (Law 33 mekanisme 6).
    echo "!!! '$DB' memuat $TN tenant, $BAD di luar slug harness '$HARNESS_SLUG'."
    echo "    Skrip ini men-DROP DATABASE — data tenant itu akan HILANG PERMANEN."
    echo "    Tenant di luar harness:"
    docker exec -i "$C" psql -U postgres -d "$DB" -tAc \
      "SELECT '      - '||id FROM \"Tenant\" WHERE id <> '$HARNESS_SLUG' ORDER BY id;" 2>/dev/null
    if [ "${I_KNOW:-0}" != "1" ]; then
      echo "    ==> MENOLAK. Kalau memang disengaja: jalankan ulang dengan I_KNOW=1."
      exit 1
    fi
    # Escape hatch yang DIAM membuat orang mengetiknya sebagai refleks.
    # Yang MENCETAK korbannya membuat mereka membacanya sekali.
    echo
    echo "!!! I_KNOW=1 — GUARD DILEWATI DENGAN SENGAJA."
    echo "    Yang akan DIHAPUS PERMANEN bersama '$DB':"
    docker exec -i "$C" psql -U postgres -d "$DB" -c \
      "SELECT t.id AS tenant, (SELECT count(*) FROM \"User\" u WHERE u.\"tenantId\"=t.id) AS pengguna, (SELECT count(*) FROM journal_entries j WHERE j.tenant_id=t.id) AS jurnal FROM \"Tenant\" t ORDER BY t.id;" 2>/dev/null
    echo
  fi
fi

# CHECK_ONLY=1 — evaluasi gerbang lalu BERHENTI. Tak pernah mencapai
# ALTER/DROP/CREATE karena `exit` ada DI ATAS ketiganya, bukan karena sebuah
# flag dipatuhi belakangan. Ada supaya pertanyaan "apakah gerbang akan menolak
# di milkydb sungguhan?" bisa dijawab TANPA mempertaruhkan balapan: kalau
# sesi lain kebetulan menghapus tenant asingnya di antara pemeriksaan dan
# eksekusi, skrip biasa akan LANJUT MENGHAPUS. Mode ini menghapus taruhan itu
# secara struktural.
if [ "${CHECK_ONLY:-0}" = "1" ]; then
  echo "(CHECK_ONLY=1 — gerbang LOLOS untuk '$DB'; berhenti SEBELUM ALTER/DROP/CREATE)"
  echo "  TN=${TN:-n/a} BAD=${BAD:-n/a} slug='$HARNESS_SLUG'"
  exit 0
fi

# CACAT (ditutup 2026-08-08): skrip mengandaikan $DB SUDAH ADA. Kalau milkydb
# hilang — justru skenario pemulihan yang paling mungkin — ALTER gagal dan
# restore menolak bekerja. Jaring yang menyerah persis saat dibutuhkan.
EXISTS=$(docker exec -i "$C" psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | tr -d '[:space:]')
if [ "$EXISTS" = "1" ]; then
  PG "ALTER DATABASE $DB WITH ALLOW_CONNECTIONS false;" || { echo "ALTER failed"; exit 1; }
else
  echo "(DB '$DB' belum ada -> lewati ALTER/DROP, langsung CREATE)"
fi
PG "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null
if [ "$EXISTS" = "1" ] && ! PG "DROP DATABASE $DB;"; then
  PG "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid<>pg_backend_pid();" >/dev/null
  PG "DROP DATABASE $DB;" || { echo "DROP failed twice; re-enabling"; PG "ALTER DATABASE $DB WITH ALLOW_CONNECTIONS true;"; exit 1; }
fi
PG "CREATE DATABASE $DB;" || { echo "CREATE failed"; exit 1; }
# CACAT (ditutup 2026-08-08): dulu `psql -q >/dev/null 2>&1` TANPA ON_ERROR_STOP
# dan rc-nya tak pernah diperiksa. Snapshot rusak/terpotong akan memuat sebagian,
# psql tetap keluar 0, dan verifikasi pristine di bawah justru HIJAU — karena DB
# yang nyaris kosong memang punya Tenant=0/User=0/journal=0. Itu kombinasi
# terburuk: kerusakan yang menyamar sebagai keadaan yang diharapkan.
echo "restoring..."
_ERR=$(mktemp)
gunzip -c "$SNAP" | docker exec -i "$C" psql -U postgres -d "$DB" -q -v ON_ERROR_STOP=1 >/dev/null 2>"$_ERR"
_RC=${PIPESTATUS[1]}
if [ "$_RC" -ne 0 ]; then
  echo "!!! PEMUATAN GAGAL (rc=$_RC) — '$DB' SETENGAH JADI. Cuplikan:"
  head -10 "$_ERR" | sed 's/^/    /'
  rm -f "$_ERR"; exit 1
fi
rm -f "$_ERR"
if [ "$DB" = "milkydb" ]; then
  echo "restart api_gateway + wait health"; docker restart milkyhoop-dev-api_gateway >/dev/null
else
  echo "(DB=$DB bukan live -> gateway TIDAK disentuh)"
fi
if [ "$DB" = "milkydb" ]; then
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/api/health 2>/dev/null)
  { [ "$code" = "200" ] || [ "$code" = "401" ]; } && { echo "gateway up ($code) after ${i}s"; break; }
  sleep 1
done
fi

echo; echo "===== PRISTINE VERIFICATION ====="
TEN=$(Q "SELECT count(*) FROM \"Tenant\";")
MIG=$(Q "SELECT count(*) FROM schema_migrations;")
PR=$(Q "SELECT count(*) FROM pending_registrations;")
USR=$(Q "SELECT count(*) FROM \"User\";")
JE=$(Q "SELECT count(*) FROM journal_entries;")
echo "  Tenant=$TEN (expect 0)"
echo "  schema_migrations=$MIG (expect 214)"
echo "  pending_registrations=$PR (expect 0 — RISK: stale signup token)"
echo "  User=$USR (expect 0)"
echo "  journal_entries=$JE (expect 0)"
FAILED=0
[ "$TEN" = "0" ] || { echo "  !!! Tenant != 0"; FAILED=1; }
[ "$PR"  = "0" ] || { echo "  !!! pending_registrations != 0"; FAILED=1; }
[ "$JE"  = "0" ] || { echo "  !!! journal_entries != 0"; FAILED=1; }
# CACAT (ditutup 2026-08-08): User dihitung dan dicetak tapi TAK PERNAH di-assert.
[ "$USR" = "0" ] || { echo "  !!! User != 0"; FAILED=1; }
# CACAT (ditutup 2026-08-08): 'nol tabel' dan 'nol baris' TAK BISA DIBEDAKAN oleh
# assert di atas — snapshot yang gagal muat separuh juga menghasilkan 0/0/0/0.
# Karena itu skema harus dibuktikan ADA, bukan cuma kosong.
TBL=$(Q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
echo "  tabel=$TBL (expect >= 250)"
[ "${TBL:-0}" -ge 250 ] 2>/dev/null || { echo "  !!! tabel=$TBL — skema TIDAK LENGKAP, bukan sekadar kosong"; FAILED=1; }
# schema_migrations: dulu hanya '(note)' -> assert yang tak pernah bisa gagal.
[ "$MIG" = "214" ] || { echo "  !!! schema_migrations=$MIG, expected 214 — baseline snapshot berubah?"; FAILED=1; }
[ "$FAILED" = "0" ] && echo "PRISTINE OK" || { echo "NOT PRISTINE — STOP"; exit 1; }
