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
