# RULE — pay-group filter on every employee_id endpoint (team-access / payroll privacy)

**Established 2026-09-20. Defect class rediscovered twice in one day; written here so the third time doesn't happen.**

## The rule

**Every API endpoint that takes an `employee_id` — by-id OR nested, READ OR WRITE — MUST apply the accessible-pay-group filter.** The LIST views filtering is **not** the guarantee: the leak is always a by-id/nested detail that skips the filter while the list enforces it.

## Why it matters

Payroll privacy (e.g. a bendahara who may see Harian/Borongan payroll but **not** Staf Bulanan salaries) can only be delivered by the pay-group filter. Module-gating (READ-authz STEP 1) is all-or-nothing — grant the EMPLOYEE/PAYROLL module and the user sees *every* group; deny it and they see *none*. So "some groups, not others" lives entirely in the pay-group filter, and it must hold on **every** surface that exposes a per-employee figure.

## How to apply (one line)

`backend/api_gateway/app/services/pay_group_access.py`:
- `await employee_in_scope(conn, tenant_id, user_id, employee_id) -> bool` — for by-id/nested. Out-of-scope → **HTTP 404** (not 403), so the response doesn't confirm the employee exists. OWNER/ADMIN → always True. Unassigned (NULL pay_group) → OWNER/ADMIN-only.
- `await accessible_pay_group_filter(conn, tenant_id, user_id) -> (is_all, ids)` — for LIST endpoints. OWNER/ADMIN → `(True, None)` (no filter); limited → `(False, [pay_group_ids])`. Add `WHERE employee.pay_group_id = ANY(ids)`.

## Endpoints fixed in commit 56d892e0 (the ones that had leaked)

READ: `GET /api/employees/{id}`, `/{id}/salary-config`, `GET /api/employee-advances` (list + `/balances`), `GET /api/payroll-config/reports/monthly-recap` (per-employee breakdown + totals), `GET /api/employees` (the expense_extended roster).
WRITE: `PUT`/`DELETE /api/employees/{id}`, `PUT /{id}/salary-config`, `POST /api/employee-advances` (grant), `POST /api/employee-advances/{id}/void`.
Already-correct (list views): `GET /api/employees`, `GET /api/payroll`, `GET /api/payroll/{run}/slips`.

## Gate discipline for a new one

Two-sided: run a **red control against the unfiltered code first** and show it succeeding (a limited user reading an out-of-scope employee returns data), so you know the test reaches the path; then confirm the fix makes it 404. Owner/in-scope must still be 200 (no over-block).
