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

---
# ADDENDUM (2026-08-03) — corrections after owner review

## CORRECTION: NO migrations to apply on this deploy
An earlier step said "apply V218/V219/V220." That is WRONG. Verified: V218/V219/V220 are already on
origin/master, already in `schema_migrations`, and their columns/constraints already exist in
milkydb (and they are idempotent anyway). **This deploy applies ZERO migrations.** Do NOT run a
migration step that would re-attempt them.

## Branch state: 18 ahead / 4 BEHIND origin/master → not a fast-forward
`git log fix/dp-readiness..origin/master` = 4 commits (prior `merge(dp-readiness)` 78280162 + 3
docs). The CODE delta vs CURRENT origin/master is exactly 3 files: quotes.py (B1),
sales_invoices.py (A1), credit_notes.py (item-1). The 4 master-ahead commits did NOT touch these 3
files, so they merge cleanly. RECOMMENDED deploy approach (minimal blast radius): apply the 3 code
changes onto current master (merge the branch, or cherry-pick the 3 code hunks), leaving
harness/docs to merge without gating the gateway. Whichever: the RUNNING gateway only needs the 3
code files + a restart. Zero schema change.

## FE WALKTHROUGH REBUILD — numbered steps (execute AFTER deploy, not now)
Provenance problem: server FE build tree `/root/milkyhoop-dev/frontend` (served by
`milkyhoop-dev-frontend-1`) has git HEAD build = `main.6a02fcc0.js` but the live/working bundle is
`main.5558c404.js` — `git ls-files --deleted` shows the committed assets missing. So the live
bundle's source is unknown; a walkthrough on it proves nothing about current source. Rebuild from a
pinned source commit:
1. Confirm the FE working tree state (done 2026-08-03): Mac /Users/antoniwan/milkyhoop/frontend/web
   is CLEAN + complete at 2bd845159 (no deleted .tsx). The deletions are BUILT ASSETS in the SERVER
   tree, not source — so `git restore` of the server tree only brings back the STALE 6a02fcc0 build,
   NOT a fresh one. Prefer rebuild-from-source over git-restore.
2. Owner: pin the FE source commit to build (candidate: a non-WIP FE commit; 2bd845159 is a
   backup-wip — confirm the intended pin).
3. On Mac: `cd frontend/web && git checkout <pinned> && npm ci && npm run build`
   (node v18.20.8 present; env from `.env.local` REACT_APP_API_URL= empty → relative URLs; no
   `.env.production` needed — CRA loads .env.local in prod builds too). Confirm no other build
   prereqs error.
4. Verify the new `build/static/js/main.<hash>.js` hash ≠ `5558c404` (proves fresh source).
5. Deploy to the server FE tree: overwrite `/root/milkyhoop-dev/frontend/{static,index.html,
   asset-manifest.json,manifest.json,service-worker.js,workers,icons}` with the fresh build (this
   makes the tree consistent — no leftover 6a02fcc0/5558c404). Then `docker compose build frontend
   && docker compose up -d frontend`.
6. Verify served hash changed: `curl -s localhost:3001/asset-manifest.json | grep main` (or via
   milkyhoop.com). Purge Cloudflare (owner) + advise SW unregister/hard-reload.
7. THEN do the UI walkthrough — it now reflects the pinned source.

## CI PREREQUISITE for C3 (PDF text assertion)
`scripts/e2e/dp_flow` step 1 C3 extracts rendered PDF text via `scripts/e2e/pdf_text.sh` (pdfminer;
WeasyPrint subsets fonts so raw grep is useless). CI MUST provide pdfminer.six: either bake it into
the gateway image, or run the suite through `test_gateway.sh` (which pre-installs it), or give the
throwaway container internet. If pdfminer is absent, pdf_text.sh EXITS 3 and C3 FAILS HARD (by
design — never a silent skip). Do not "fix" that by making C3 optional.
