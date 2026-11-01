#!/bin/bash
# apply_mig.sh <migration file name, e.g. V295__x.sql> [--require-marker <file-rel-to-app> <text>]
# Applies ONE migration from the LIVE main tree to prod milkydb: refuses if already recorded; optional
# precondition = a marker must be present in the RUNNING container's file (code-first ordering);
# pg_dump backup first; then ONE transaction (psql -1): schema_migrations INSERT + the migration together.
#
# MIGRASI-AMAN (1 Nov 2026, BACKEND2; GO MASTER, izin pemilik langsung untuk penggantian alat prod):
# - lock_timeout (bawaan 5s) + statement_timeout (bawaan 15min) lewat SET LOCAL: ALTER yang menunggu kunci tabel
#   yang sedang dipakai GAGAL CEPAT (rollback, nol catatan) -- tak menahan antrean tulis pengguna di belakangnya.
# - catatan schema_migrations ditulis PERTAMA di transaksi yang SAMA dengan migrasi: gagal mana pun = keduanya batal
#   (dulu INSERT di psql terpisah sesudahnya -> "applied but not recorded" -> migrasi bisa terulang). PK version
#   juga menolak pelari kedua yang bersamaan.
# - menolak berkas berisi kendali transaksi (BEGIN;/COMMIT;/ROLLBACK;/START TRANSACTION) -- COMMIT di dalam -1
#   menutup transaksi luar lebih awal dan memecah atomisitas -- dan CREATE INDEX CONCURRENTLY (tak bisa di transaksi).
# - kata sandi dibaca dari env kontainer postgres (POSTGRES_PASSWORD), tak lagi tertulis di skrip.
# - sasaran bisa diganti lewat env MIG_* HANYA untuk uji di kontainer sekali-pakai (scripts/ops/uji-apply-mig.sh).
# Kode keluar: 3 nama/berkas ditolak · 4 penanda kode tak ada · 5 sudah tercatat · 6 cadangan gagal · 7 migrasi gagal
# (rollback, nol catatan) · 8 sesudah sukses catatan tak terbaca · 9 kendali transaksi di berkas · 10 CONCURRENTLY.
set -uo pipefail
F=${1:?usage: apply_mig.sh <V…__x.sql> [--require-marker <file-rel-to-app> <text>]}; shift
MAIN=${MIG_MAIN:-/root/milkyhoop-dev}; P=${MIG_PG:-milkyhoop-dev-postgres-1}; G=${MIG_GW:-milkyhoop-dev-api_gateway}
DB=${MIG_DB:-milkydb}; BK=${MIG_BACKUP_DIR:-/root/backups}; BY=${MIG_BY:-BACKEND}
LOCK=${MIG_LOCK_TIMEOUT:-5s}; STMT=${MIG_STATEMENT_TIMEOUT:-15min}
psqlc() { docker exec -i "$P" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -U postgres -d "$0" -v ON_ERROR_STOP=1 "$@"' "$DB" "$@"; }

[[ "$F" =~ ^V[0-9]+__[A-Za-z0-9_]+\.sql$ ]] || { echo "REFUSE: name '$F' is not V<n>__<name>.sql"; exit 3; }
[[ "$F" == *_ROLLBACK.sql ]] && { echo "REFUSE: $F is a ROLLBACK file -- reversal is a separate, owner-approved step"; exit 3; }
[[ "$LOCK" =~ ^[0-9]+(ms|s|min)$ && "$STMT" =~ ^[0-9]+(ms|s|min)$ && "$BY" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "REFUSE: bad MIG_* value"; exit 3; }
SRC=$MAIN/backend/migrations/$F
[ -f "$SRC" ] || { echo "REFUSE: $SRC not in live main tree"; exit 3; }
if grep -nE '^[[:space:]]*(BEGIN|COMMIT|ROLLBACK|START[[:space:]]+TRANSACTION)[[:space:]]*;' "$SRC"; then
  echo "REFUSE: transaction control in $F (would break the single transaction) -- remove it; apply_mig wraps the file"; exit 9
fi
if grep -niE 'CONCURRENTLY' "$SRC"; then
  echo "REFUSE: CONCURRENTLY cannot run inside a transaction -- apply that statement by hand, outside this tool"; exit 10
fi
if [ "${1:-}" = "--require-marker" ]; then
  n=$(docker exec "$G" grep -cF -- "$3" "/app/backend/api_gateway/app/$2" 2>/dev/null || true); n=$(echo "$n" | head -1)
  [ "${n:-0}" -ge 1 ] || { echo "REFUSE: code-first marker '$3' not in RUNNING $2 -- migration NOT applied"; exit 4; }
  echo "precondition: running code carries marker ($n)"
fi
have=$(psqlc -Atc "select count(*) from schema_migrations where version='$F'")
[ "$have" = "0" ] || { echo "REFUSE: $F already recorded"; exit 5; }
TS=$(date -u +%H%M%S); V=${F%%__*}; V=$(echo "$V" | tr 'A-Z' 'a-z'); D=$DB-pre-$V-$TS.dump
docker exec "$P" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U postgres -Fc "$0" > "/tmp/$1"' "$DB" "$D" && docker cp "$P:/tmp/$D" "$BK/" \
  && docker exec "$P" rm -f "/tmp/$D" || { echo "REFUSE: backup failed"; exit 6; }
ls -la "$BK/$D" | awk '{print "backup", $5, $9}'
SUM=$(sha256sum "$SRC" | cut -d' ' -f1)
W=$(mktemp); trap 'rm -f "$W"' EXIT
cat > "$W" <<EOF
SET LOCAL lock_timeout = '$LOCK';
SET LOCAL statement_timeout = '$STMT';
INSERT INTO schema_migrations(version, checksum, status, applied_at, applied_by) VALUES ('$F', '$SUM', 'applied', now(), '$BY');
\\i /tmp/mig-$V.sql
EOF
docker cp "$SRC" "$P:/tmp/mig-$V.sql" && docker cp "$W" "$P:/tmp/mig-$V-wrap.sql" || { echo "REFUSE: copy into container failed"; exit 6; }
psqlc -q -1 -f "/tmp/mig-$V-wrap.sql" > "/tmp/mig_$V.log" 2>&1; rc=$?
docker exec "$P" rm -f "/tmp/mig-$V.sql" "/tmp/mig-$V-wrap.sql"
if [ $rc -ne 0 ]; then
  grep -qi "lock timeout" "/tmp/mig_$V.log" && echo "LOCK_TIMEOUT: a table is in use (waited $LOCK) -- retry outside working hours"
  grep -qi "statement timeout" "/tmp/mig_$V.log" && echo "STATEMENT_TIMEOUT: a statement ran longer than $STMT"
  echo "FAILED: psql rc=$rc (transaction rolled back, recorded=$(psqlc -Atc "select count(*) from schema_migrations where version='$F'"))"
  tail -5 "/tmp/mig_$V.log"; exit 7
fi
rec=$(psqlc -Atc "select version||' '||applied_at from schema_migrations where version='$F'") && [ -n "$rec" ] \
  || { echo "WARN: committed but record not readable -- check schema_migrations by hand"; exit 8; }
echo "recorded $rec"
echo "MIG|$F|applied"
