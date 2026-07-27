# RUNBOOK — Deploy #2 (branch fix/dp-readiness → master) + post-deploy DB + test-gateway pattern

**Status:** PREPARED. Do NOT execute the deploy without owner GO (E1). This is the first real deploy
of everything accumulated on `fix/dp-readiness`.

## E1 decision (owner): DEPLOY NOW, do NOT batch with team-invite
Rationale (owner): the branch already carries V218/V219/V220 + pool-lock fix + permission +
harness + BATCH1 (A1/B1). Adding team-invite ENLARGES one deploy's blast radius. "One restart vs
two" is weak at zero users (restart cost ≈ 0). Team-invite is not a one-liner (__SYSTEM__ role
lookup, orphan team_invitations, zero INSERT) → could take days. The cheapest deploy window is NOW;
it closes when early adopters arrive. So: ship BATCH1 (+ the already-accumulated branch) now.

## PRE-DEPLOY
1. **Review the WHOLE branch diff**, not just BATCH1's ~5 lines: `git -C /root/milkyhoop-dev
   fetch origin && git log --oneline origin/master..fix/dp-readiness` and `git diff
   origin/master..fix/dp-readiness --stat`. Confirm every change is intended (V218/V219/V220
   migrations, backend fixes, harness).
2. **Fresh DB snapshot BEFORE deploy** (rollback point): the `~/milky-backup.sh` DB layer, or a
   direct `pg_dump | gzip | gpg` of milkydb. Verify it decrypts + counts tables.
3. **Reverse-DDL ready** for V218/V219/V220 (down scripts) in case a migration must be rolled back.
4. Confirm `git status` of main tree /root/milkyhoop-dev is clean (master, no stray WIP) before
   any reset.

## DEPLOY (only after GO)
1. Merge `fix/dp-readiness` → `master` (review gate).
2. Main tree: `git -C /root/milkyhoop-dev fetch origin && git -C /root/milkyhoop-dev checkout
   master && git -C /root/milkyhoop-dev reset --hard origin/master` (guard: status must be clean
   first).
3. Apply migrations V218/V219/V220 (fetch-before-apply the V-numbers; shared milkydb).
4. Reload code: **`docker restart milkyhoop-dev-api_gateway`** — NOT bare `up -d`, NOT
   `--force-recreate`. Clear `__pycache__` if stale.

## POST-DEPLOY VERIFY
- `curl -s -o /dev/null -w '%{http_code}' localhost:8001/api/health` → 200.
- `migrate.sh` verify: schema_migrations = 214 tracked (or the expected count post V218-V220).
- `docker inspect milkyhoop-dev-api_gateway --format '{{.State.StartedAt}}'` shifted (restart took).
- Smoke a NON-deposit route (e.g. GET /api/customers, GET /api/sales-invoices) → 200.
- Smoke the two BATCH1 fixes on the real gateway: GET /api/sales-invoices/{id}/applicable-deposits
  → 200 (not 500); GET /api/quotes/{id} → payment_* non-null.

## E2 — POST-DEPLOY DB SEQUENCE (owner-agreed)
DO NOT restore before deploy. Correct order:
1. **Deploy** (above).
2. **Restore preharness** (pristine, 0 tenants) via `scripts/e2e/dp_flow/restore_preharness.sh`.
3. **Run step -1 ONLY** (`bash scripts/e2e/dp_flow/step_-1_provision.sh`) — creates the tenant +
   master data, ZERO transactions.
This leaves a tenant at the STARTING state (not a finished flow) — the correct basis for manual UI
walk-throughs (which need a tenant at various stages, not a completed run).

## TEST-GATEWAY PATTERN (permanent — `scripts/e2e/test_gateway.sh`)
For testing a worktree fix WITHOUT deploying to live (the live gateway bind-mounts
/root/milkyhoop-dev master, read-only, no reload):
- `bash scripts/e2e/test_gateway.sh up`   → starts `mh-test-gw` on :8002, same image + network,
  bind-mounting the CURRENT worktree; pre-installs pdfminer.six for pdf_text.sh.
- Run the suite against it: `B=http://localhost:8002/api bash scripts/e2e/dp_flow/run_all.sh`.
- `bash scripts/e2e/test_gateway.sh down` → removes it.
- `scripts/e2e/pdf_text.sh <pdf>` extracts rendered PDF text (pdfminer; WeasyPrint fonts are
  subsetted so raw grep is useless).
- ⚠️ **HAZARD (written):** it connects to the ONE LIVE milkydb — there is no separate test DB, and
  run_all RESTORES/DRIVES it. Safe ONLY pre-launch. GUARD in test_gateway.sh REFUSES to start if
  milkydb has >1 tenant or any tenant outside the harness slug. After early adopters exist, this
  pattern must NOT be used against the live milkydb — stand up a dedicated test DB first.
