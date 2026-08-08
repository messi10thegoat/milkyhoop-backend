#!/bin/bash
# restore_preharness.sh — restore milkydb from the pristine preharness snapshot and
# verify it is pristine. RESTORE ONLY (no steps). ALLOW_CONNECTIONS-false to win the
# drop-vs-pool race; re-enables connections on any failure. Then asserts the pristine
# invariants the single-shot run depends on.
# DIUJI DUA ARAH: 2026-08-08   <- penanda WAJIB, seragam lintas jaring (Law 33 aturan 1)
#   Diuji di DB KLON lewat DB= (live TIDAK disentuh):
#   (a) snapshot sah      -> PRISTINE OK, Tenant/User/journal = 0, migrasi 214
#   (b) snapshot dipotong -> GAGAL KERAS di pemuatan (rc != 0), bukan "PRISTINE OK"
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
