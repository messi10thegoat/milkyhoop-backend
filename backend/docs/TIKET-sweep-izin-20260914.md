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
