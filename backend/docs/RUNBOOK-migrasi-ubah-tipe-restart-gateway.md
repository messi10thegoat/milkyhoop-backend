# RUNBOOK — Migrations that change a column's type → gateway restart is MANDATORY

**Rule:** if a migration changes the TYPE (`ALTER COLUMN … TYPE`) of a column that any gateway query selects or compares, then IMMEDIATELY after the migration:

1. `find /root/milkyhoop-dev/backend -name '*.pyc' -delete; find … -name __pycache__ -exec rm -rf {} +`
2. `docker restart milkyhoop-dev-api_gateway`, then wait for `healthz` 200 on :8001.
3. Run the post-restart gate against the live module and a NEW connection (not a connection from before the migration).

**Why:** the asyncpg pool caches prepared statements and their plans per connection. After an ALTER TYPE, old connections throw `InvalidCachedStatementError` or "operator does not exist: uuid = text" until they are replaced. The V247 dry run showed 3 red results that were entirely this artefact. They disappeared after `reload_schema_state()` and the statement cache was cleared. These are not code defects, but users see them as 500s until a restart.

**Migrations that do NOT need a restart:** function/trigger/constraint only (V244 triggers, V245/V246 `CREATE OR REPLACE FUNCTION`).
**Migrations that DO:** V247 (`credit_notes`/`customer_deposits.customer_id` varchar → uuid).

**Deploy order for a type change (V247 pattern):**
1. Deploy 1: code that works with BOTH types (`::text` on both sides, `str()` on readers). Verify it on the old type.
2. Owner's window: migration (asserts RAISE when data is dirty) → restart immediately → post-restart gate.
3. If a post-restart check is red: do not patch in production. Evaluate the ROLLBACK file (lossless, back to varchar) and report. If the owner's screen cannot be used, ROLLBACK first and report afterwards.

**Gate lesson:** a gate that picks its subject automatically must assert that the subject HAS the data under test. If it doesn't, the verdict is TAK SAH, not green. The first V247 run of item 4 got 0 == 0 == 0, which proves "no 500", not "correct content".

**V247 record (2026-09-13 21:37 UTC):** migration 1 s, healthz 200 after about 16 s. Post-restart gate 23/23. Journal tab on subjects with journals 13/13, sabotage turns red.
