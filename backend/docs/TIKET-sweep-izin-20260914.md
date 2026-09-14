# TICKET — Permission sweep (PermissionMiddleware) · 14 Sep 2026

**Status:** stages 1–2 LIVE, (b) and (c) awaiting a decision, stage 3 not yet.

## Inventory (introspection of the live app.routes × ROUTE_PERMISSIONS, `scripts/inventaris_izin.py`)
- 1081 route×method before the sweep:
  - SKIP 45;
  - mapped 390;
  - **no pattern 646** (write 297, read 349);
  - 28 patterns with no route;
  - 21 methods not covered.
- The decision mechanism, read from the code:
  - no pattern → **allowed without a check**;
  - (before stage 1) error in the checker → allowed;
  - RBACMiddleware only checks subscription TIER, not the business role.
- **Labels (MASTER's correction):**
  - the ACTIVE COLLABORATOR account in kaos-biru = `uji.nonowner` (created 3 Sep), the **agent session's non-owner TEST ACCOUNT**, not a real team member;
  - the 66 "Permission denied" entries in 72h are most likely agent test traffic; do not read them as human use without evidence.
  - The gap is **reachable today, but by a test account**; the owner says a team invite is not close → urgency does not rise.

## STAGE 1 (live `b0aeed0a`)
- Checker error → 403 PERMISSION_CHECK_ERROR.
- 3 test routers removed (1081→1077).
- The swallowed skip was restored and anchored to `^/api/team-members/roles/list$`.

Gate: `scripts/gerbang_izin_tahap1.py`.

## STAGE 2 (live — see commit)
Owner decision (a): reuse the 15 DB modules that already have role_permissions rows. The **143 patterns** for money-moving writes were generated from written rules (`scripts/bangkit_pola_tahap2.py`):
- router → module;
- method/path verb → action.

Known rule artefact: `POST /api/cheques/receive` → P (the verb "receive"), not C. For non-owners it is refused either way (BANK R); change only if the owner asks.

Gate `scripts/gerbang_izin_tahap2.py`:
- coverage from the live route table;
- independent action check;
- the owner reaches the 5 daily flows + 9 new modules;
- the test account gets 403 on cheques/issue and customers/merge;
- old: 3 RED;
- sabotage by removing a pattern: RED.

## OPEN
1. **(b) 26 middleware modules without role_permissions rows:** EXPENSE, QUOTE, SALES_ORDER, CREDIT_NOTE, CUSTOMER_DEPOSIT, VENDOR_DEPOSIT, STOCK_ADJUST, WAREHOUSE, UNIT, FIXED_ASSET, PERIOD, BUDGET, INTERCOMPANY, LEDGER, AR_AGING, BOM, WORK_ORDER, WORK_CENTER, MATERIAL_ISSUE, FG_RECEIPT, EMPLOYEE, BPJS, PAY_GROUP, SALARY_COMPONENT, DEBIT_NOTE, TABLES.
   - Today every non-owner role is refused on mapped routes in those modules.
   - A role×action matrix proposal goes to MASTER → owner. **Not installed.**
2. **(c) 16 chat & document_intake routes still WITHOUT a pattern:** not an exemption.
   - Decision: financial actions are checked against the TARGET module's permission on behalf of the user.
   - Measure the internal path first → design → MASTER.
3. **Patterns the FE calls without a BE route (404, not a permission issue):** labelled, NOT deleted. → a separate FRONTEND/BE ticket.
   - `^/api/bill-payments/[^/]+$` PATCH/PUT (FE 7×)
   - `^/api/payroll/[^/]+/journal-entries$` (FE 1×)
   - `^/api/payroll/summary$` (FE 1×)
   - `^/api/payment-requests/[^/]+$` (FE 1×)
   - `^/api/approvals` (FE 1×)
   - material-issues / fg-receipts detail: the FE calls the list 4× each; only the list route exists.
4. Stage 3: flip the default (unmapped write → 403) + a gate "every WRITE has a pattern OR is in a declared exemption set".


---

## STAGE (c) — X-Source bypass RETIRED (14 Sep 2026, commit a9b779e4)

**Reason for retirement:** the `X-Source=action_executor` bypass in AuthMiddleware set `role=ADMIN` and skipped auth for any request carrying `X-Source` + `X-Tenant-ID`. With `X-User-ID` it acted as any user — a header-trust hole. The gRPC `action_executor` service that used it is dead: no container, host unresolved from the gateway, 0 "Internal service auth bypass" events in 30 days. Chat executes financial actions through the user's own JWT (the `is_direct` REST path), which is untouched.

**Owner decision (via MASTER):** (A) retire — one coherent change, not a shared secret.

**Removed:**
- the AuthMiddleware bypass block;
- the gRPC-client calls in unified_chat.py (confirm non-direct → readable `ACTION_EXECUTOR_RETIRED` error; `get_action_status` now reads `pending_actions.status` from the DB);
- the deprecated, unmounted `action_chat.py` (6 dead references + import) — nothing imports it (main.py include commented since the v3 migration);
- a stray tracked backup `unified_chat.py.before_manual_fix`;
- the `action_executor` service + its two gateway env lines in docker-compose.yml.

The service code dir under `backend/services/action_executor/` stays (git history), now with no auth contract into the gateway.

**Baseline before change:** the 4 non-direct `pending_actions` ever created (2–3 Sep) are all expired/cancelled, none executed; the gRPC host has been unresolved, so non-direct confirms already failed. Removal is behavior-preserving.

**Gate `scripts/gerbang_xsource.py` (two-sided, real AuthMiddleware):**
- new: X-Source+X-Tenant+X-User-ID(owner) with no Bearer → 401; baseline no-auth → 401; unified_chat 0 references; is_direct JWT path intact.
- old: the same X-Source request → 200 (bypass proven live).
- LIVE after deploy: X-Source gate 5/5, stage 1 gate 10/10, stage 2 gate 7/7, 0 references in routers, action_chat absent, healthz 200 (app.main imports).
- compose config rc=0, action_executor gone, all other services intact.

**Post-fix public probe:** `POST https://milkyhoop.com/api/expenses` with X-Source + a FAKE X-User-ID → **401**; same via :8001 → 401.

**nginx (for a follow-up ticket, NOT urgent now the bypass is gone):** `/etc/nginx/sites-available/milkyhoop.conf`, `location /api → proxy_pass http://127.0.0.1:8001`. It does NOT strip client-supplied `X-Source` / `X-User-ID` / `X-Tenant-ID` (no proxy_set_header removes them). Before this fix that made the hole externally reachable. Now that the bypass is removed the app rejects it regardless, but stripping those headers at nginx is worth adding as defense-in-depth.


---

## STAGE 2 (b) — role_permissions for the 26 row-less modules (14 Sep 2026, migration V251, commit eec1eacc)

Before: 26 modules used by the middleware had NO role_permissions rows → every non-owner role was refused on their mapped routes. Owner decision (via MASTER): each module COPIES its analog module's per-role actions, then 6 corrections.

**Analog map:** EXPENSE/DEBIT_NOTE←BILL · QUOTE/SALES_ORDER/CREDIT_NOTE/TABLES←INVOICE · CUSTOMER_DEPOSIT←RECEIPT · VENDOR_DEPOSIT←PAYMENT · STOCK_ADJUST/WAREHOUSE/UNIT/BOM/WORK_ORDER/WORK_CENTER/MATERIAL_ISSUE/FG_RECEIPT←PRODUCT · FIXED_ASSET/INTERCOMPANY/LEDGER/PERIOD←JOURNAL · BUDGET/AR_AGING←REPORT · EMPLOYEE/BPJS/PAY_GROUP/SALARY_COMPONENT←PAYROLL.

**6 corrections (separation of duties / accounting):**
1. CASHIER & STORE_STAFF on EXPENSE = C,R only (petty cash).
2. PERIOD: C/U/P/V only ADMIN & FINANCE_MGR; every other role R.
3. VIEWER: R on FIXED_ASSET, LEDGER, PERIOD, INTERCOMPANY.
4. BUDGET: ACCOUNTANT C,R,U; FINANCE_MGR C,R,U,A.
5. EMPLOYEE/BPJS/PAY_GROUP/SALARY_COMPONENT: only HR_PAYROLL & ADMIN (salary confidentiality — COLLABORATOR removed).
6. CREDIT_NOTE: SALES & STORE_STAFF no P and no V (a credit note reduces receivables → posted by ACCOUNTANT/FINANCE_MGR/BENDAHARA/ADMIN).

**Cache:** role_permissions has no cache invalidation → the gateway was restarted after applying V251.

**Gate `scripts/gerbang_matriks.py` (dry-run, ROLLBACK):** 11/11 — the diff of every (module, role) against its analog == exactly the 6 declared corrections; nothing undeclared; every correction actually occurred; 26 modules row-less again after rollback.
**Live after apply + restart (`scripts/ukur_can_hidup.py`, real policy engine):** 15/15 `can()` checks — e.g. CASHIER can create but not void an expense; ACCOUNTANT can't create a period but can read it; VIEWER reads but can't create a fixed asset; COLLABORATOR can't read an employee; SALES can't post a credit note; FINANCE_MGR can approve a budget; OWNER can do everything (bypass). Stage 1 gate 10/10, stage 2 gate 7/7 still green.

**Row count:** 57 rows across the 6 spot-checked modules; migration registered in schema_migrations.


---

## STAGE 3 — default-closed for unmapped WRITE (14 Sep 2026, commit ba89af36)

The permission default is now CLOSED for writes. In PermissionMiddleware, a WRITE (POST/PUT/PATCH/DELETE) with no matching pattern and not in the declared WRITE_EXEMPT set:
- OWNER → passes (bypass, consistent with `can()`);
- any other role → **403 PERMISSION_UNMAPPED** + a warning log.
READ with no pattern stays OPEN (recorded here as remaining work; reads don't mutate).

**WRITE_EXEMPT (declared in code with a per-line reason):** session logout · self user profile/favorites · onboarding (pre-provision) · invite accept/decline (user has no role yet) · device self-management · raw uploads · chat entry (`/api/v3/chat/`, legacy `/chat/`, setup/tenant/public chat) — the chat endpoint writes nothing privileged; a financial action it triggers is executed by re-calling the kernel with the user's JWT, where THAT route's permission is enforced (see stage (c)).

**Coverage baseline `scripts/izin_write_baseline.json` (121 writes):** the business writes that are currently unmapped. After the flip they are OWNER-ONLY (fail-closed). They are recorded so the coverage gate reds only on a NEW write that is neither mapped, nor exempt, nor in this baseline. Mapping these to modules for non-owner roles is incremental follow-up per module (owner-only until then).

**Gate `scripts/gerbang_tahap3.py` (from the live route table + real middleware/engine):**
- coverage: every live WRITE = pattern OR write_exempt OR baseline;
- owner: 5 daily flows + an unmapped write → 200;
- non-owner test account: unmapped write → 403 PERMISSION_UNMAPPED; exempt write + chat → pass; READ unmapped → 200 (open);
- old middleware: default-open (non-owner unmapped → 200);
- sabotage (remove one pattern): coverage RED (the route leaves `mapped`, isn't in baseline).
Live after deploy: gate 7/7, stage 1 10/10, stage 2 7/7.

**Owner PERMISSION_UNMAPPED in logs:** 0 in the window since deploy. **24h check owed** (MASTER condition 5): confirm 0 owner-triggered PERMISSION_UNMAPPED after a full day; if any appears, that owner path needs a pattern.

**Remaining after stage 3:**
- READ default still open (log-only for now).
- The 121 baseline writes are owner-only; map per module for non-owner roles as prioritised.
- nginx: strip client X-Source / X-User-ID, but NOT X-Tenant-ID (FE uses it — measure first).
- FE-calls-no-BE-route → separate ticket for FRONTEND (MASTER forwards).


---

## STAGE 3 ratchet + nginx header strip (14 Sep 2026)

**Coverage ratchet (commit b5e792b7):** the stage-3 coverage gate is now a RATCHET. The baseline (`scripts/izin_write_baseline.json`, 121) may only SHRINK: the gate reds if any live unmapped-non-exempt write is outside the baseline OR the baseline count exceeds 121. A write that gains a pattern or an exemption (baseline shrinks) stays green. Sabotage "add one baseline entry" → RATCHET red; "remove one pattern" → coverage + ratchet red. Gate 8/8.

**nginx header strip (config, backup `.bak-20260914`):** measured senders first — the web FE sends NONE of X-Source / X-User-ID / X-Tenant-ID; only backend-to-backend internal calls set X-Tenant-ID and they go direct to :8000, NOT through nginx (which fronts external :443 → :8001). So `location /api` now strips **X-Source** and **X-User-ID** from proxied requests (`proxy_set_header … "";`). **X-Tenant-ID is NOT stripped** (not proven unused by an external client). `nginx -t` ok, `nginx -s reload` (not restart). Post-reload: gateway healthz 200, FE root 200, a real API 401 (proxy fine), public X-Source+fake-user probe → 401. Defense-in-depth on top of the app-layer bypass removal (stage c).
Observed (pre-existing, not from this change): nginx warns `protocol options redefined for [::]:443` at milkyhoop.conf:53 — a duplicate http2/ssl option, worth a separate tidy.

**Deferred to team-invite time (owner decision, recorded):**
- mapping the 121 baseline writes to modules for non-owner roles (owner-only until then);
- flipping the READ default (reads stay open for now).

**Owed:** the 24h owner-PERMISSION_UNMAPPED check — report tomorrow (0 so far).
