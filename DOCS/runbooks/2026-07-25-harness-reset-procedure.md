
---
## HARNESS RESET PROCEDURE (named; TESTED green 2026-07-25)
Precondition: milkydb_preharness_<ts>.sql.gz (post-migration, 0-tenant restore point).
Why this method: api_gateway + auth + grpc hold a superuser connection pool to milkydb, so a plain
DROP DATABASE races their reconnects. `ALTER DATABASE ... ALLOW_CONNECTIONS false` blocks ALL new
connections INCLUDING superuser -> the drop wins deterministically. Chosen over stop-all-containers
(would need to enumerate every milkydb consumer) and over terminate-only (racy).

Steps (all via `docker exec milkyhoop-dev-postgres-1 psql -U postgres`):
  1. -d postgres -c "ALTER DATABASE milkydb WITH ALLOW_CONNECTIONS false;"
  2. -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                     WHERE datname='milkydb' AND pid<>pg_backend_pid();"
  3. -d postgres -c "DROP DATABASE milkydb;"   # if it fails, connections leaked in -> re-run 1+2
  4. -d postgres -c "CREATE DATABASE milkydb;" # new DB defaults allowconn=true
  5. gunzip -c milkydb_preharness_<ts>.sql.gz | docker exec -i ... psql -U postgres -d milkydb
  6. docker restart milkyhoop-dev-api_gateway  # fresh pool; wait /health 200 (curl --retry)

Verify after: tenants=0; schema_migrations=214 (incl 2 sentinels); migrate.sh verify OK 0 drift;
compute_ap_outstanding contains 'vd_applied_debits' (V218 present).
TEST RESULT 2026-07-25: terminated=5, DROP+CREATE ok, 0 restore errors, /health 200, all verify green.

## ⚠️ HAZARD — ALLOW_CONNECTIONS=false failure window (recovery)
Step 1 (`ALTER DATABASE milkydb WITH ALLOW_CONNECTIONS false`) blocks ALL new connections,
INCLUDING superuser, to milkydb. If the procedure aborts AFTER step 1 but the DB still exists
(step 3 DROP failed / was skipped, or the operator stops mid-run), milkydb is left EXISTING but
UNCONNECTABLE — every `psql -d milkydb` (and the app pool) will be refused, which can look like
"database is gone" but is not.

RECOVERY (do NOT try to connect to milkydb — you cannot): connect to the always-present
`postgres` maintenance DB and flip the flag back:
    docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d postgres \
      -c "ALTER DATABASE milkydb WITH ALLOW_CONNECTIONS true;"
Then either resume the reset (re-run steps 1-6) or, if you were aborting, milkydb is reachable
again. The same one-liner is the fix for any "milkydb refuses all connections after a reset
attempt" symptom. Note the flag survives across the DROP/CREATE boundary only if DROP never ran;
a successful CREATE (step 4) always yields allowconn=true, so the window is exactly
[after step 1] .. [before a successful DROP+CREATE].
