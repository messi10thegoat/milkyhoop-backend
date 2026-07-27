#!/bin/bash
# restore_preharness.sh — restore milkydb from the pristine preharness snapshot and
# verify it is pristine. RESTORE ONLY (no steps). ALLOW_CONNECTIONS-false to win the
# drop-vs-pool race; re-enables connections on any failure. Then asserts the pristine
# invariants the single-shot run depends on.
set -uo pipefail
SNAP=${SNAP:-/root/milkydb_preharness_20260726_022045.sql.gz}
C=milkyhoop-dev-postgres-1
PG(){ docker exec -i "$C" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "$1"; }
Q(){ docker exec -i "$C" psql -U postgres -d milkydb -tAc "$1" | tr -d '[:space:]'; }

echo "===== RESTORE milkydb from $SNAP ====="
PG "ALTER DATABASE milkydb WITH ALLOW_CONNECTIONS false;" || { echo "ALTER failed"; exit 1; }
PG "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='milkydb' AND pid<>pg_backend_pid();" >/dev/null
if ! PG "DROP DATABASE milkydb;"; then
  PG "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='milkydb' AND pid<>pg_backend_pid();" >/dev/null
  PG "DROP DATABASE milkydb;" || { echo "DROP failed twice; re-enabling"; PG "ALTER DATABASE milkydb WITH ALLOW_CONNECTIONS true;"; exit 1; }
fi
PG "CREATE DATABASE milkydb;" || { echo "CREATE failed"; exit 1; }
echo "restoring..."; gunzip -c "$SNAP" | docker exec -i "$C" psql -U postgres -d milkydb -q >/dev/null 2>&1
echo "restart api_gateway + wait health"; docker restart milkyhoop-dev-api_gateway >/dev/null
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/api/health 2>/dev/null)
  { [ "$code" = "200" ] || [ "$code" = "401" ]; } && { echo "gateway up ($code) after ${i}s"; break; }
  sleep 1
done

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
[ "$MIG" = "214" ] || echo "  (note) schema_migrations=$MIG, expected 214 — verify this is the right baseline"
[ "$FAILED" = "0" ] && echo "PRISTINE OK" || { echo "NOT PRISTINE — STOP"; exit 1; }
