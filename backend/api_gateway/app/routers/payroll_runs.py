"""
Payroll Router — CRUD + Workflow + Journal Posting

Endpoints: list, create, get, update, calculate, submit, approve, reject, post, void, slips
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
from decimal import Decimal
import logging
import asyncpg

from ..schemas.payroll import (
    CreatePayrollRequest,
    UpdatePayrollRequest,
    VoidPayrollRequest,
    RejectPayrollRequest,
)
from ..services.payroll_calc import (
    calculate_employee_slip,
    get_bpjs_config,
    get_ytd_data,
    PayrollInputError,
)
from ..services.role_resolver import AccountRole, resolve_account_id_by_role, AccountRoleUnmappedError
from ..services.pay_group_access import get_accessible_pay_group_ids, get_user_role_code

logger = logging.getLogger(__name__)
router = APIRouter()


# Canonical "assigned + effective salary components for (employee, date)" query, shared by
# calculate (reads component_id/amount/percentage) and the GET /{id} eligibility projection
# (reads the sc.* metadata) so the projection shows EXACTLY the set the engine consumes --
# one source, not two SELECTs that agree today and drift later. Predicate is byte-identical
# to the pre-refactor calculate query (effective_date <= $3 <= end_date, NO is_active filter,
# ORDER BY sc.sort_order); proven equivalent across all kaos + grapgrap employees at
# config-boundary dates before this landed. NOTE a third copy still lives at the create/preview
# path (~:207) -- left as its own ticket, folded in only when it has its own gate.
ASSIGNED_COMPONENTS_SQL = """SELECT esc.component_id, esc.amount, esc.percentage,
       sc.code, sc.name, sc.type, sc.category, sc.calculation_method, sc.sort_order
FROM employee_salary_config esc
JOIN salary_components sc ON sc.id = esc.component_id
WHERE esc.tenant_id = $1 AND esc.employee_id = $2
  AND esc.effective_date <= $3
  AND (esc.end_date IS NULL OR esc.end_date >= $3)
ORDER BY sc.sort_order"""


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


async def _resolve_wage_account(conn, tenant_id, role_key, default_role="SALARY_EXPENSE"):
    """Resolve a component's earning destination CoA by ROLE (Law 27, V286). NULL/unknown/unmapped
    role -> the SALARY_EXPENSE default -- NEVER raise, so an unconfigured tenant's run is never blocked."""
    rk = role_key or default_role
    try:
        return await resolve_account_id_by_role(conn, tenant_id, rk)
    except Exception:
        return await resolve_account_id_by_role(conn, tenant_id, default_role)


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


async def _pg_filter(conn, ctx):
    uid = str(ctx["user_id"]) if ctx.get("user_id") else None
    tid = str(ctx["tenant_id"])
    if not uid:
        return ("VIEWER", [])
    role_code = await get_user_role_code(uid, tid, conn)
    accessible_ids = await get_accessible_pay_group_ids(uid, tid, role_code, conn)
    return (role_code, accessible_ids)


# ========== LIST ==========


@router.get("")
async def list_payroll_runs(
    request: Request,
    status: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        role_code, accessible_ids = await _pg_filter(conn, ctx)
        conditions = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        idx = 2
        if role_code not in ("OWNER", "ADMIN"):
            if not accessible_ids:
                return {"success": True, "data": [], "total": 0, "page": page}
            conditions.append(
                f"EXISTS (SELECT 1 FROM payroll_slip_lines psl JOIN employees e ON e.id = psl.employee_id WHERE psl.payroll_id = payroll_runs.id AND e.pay_group_id = ANY(${idx}::uuid[]))"
            )
            params.append(accessible_ids)
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if year:
            conditions.append(f"EXTRACT(YEAR FROM period_start) = ${idx}")
            params.append(year)
            idx += 1
        where = " AND ".join(conditions)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM payroll_runs WHERE {where}", *params
        )
        offset = (page - 1) * page_size
        rows = await conn.fetch(
            f"""SELECT id, payroll_number, period_start, period_end, payment_date,
                       description, total_basic_salary, total_allowances, total_deductions,
                       total_net_salary, employee_count, status, created_at
                FROM payroll_runs WHERE {where}
                ORDER BY period_start DESC
                LIMIT {page_size} OFFSET {offset}""",
            *params,
        )
        return {
            "success": True,
            "data": [dict(r) for r in rows],
            "total": total,
            "page": page,
        }


# ========== CREATE ==========


@router.post("")
async def create_payroll_run(request: Request, body: CreatePayrollRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        # Pay group validation
        role_code, accessible_ids = await _pg_filter(conn, ctx)
        if role_code not in ("OWNER", "ADMIN") and body.employee_ids:
            if not accessible_ids:
                raise HTTPException(403, detail="No pay group access to create payroll")
            invalid = await conn.fetchval(
                "SELECT COUNT(*) FROM employees WHERE id = ANY($1::uuid[]) AND tenant_id = $2 AND pay_group_id != ALL($3::uuid[])",
                [str(e) for e in body.employee_ids],
                ctx["tenant_id"],
                accessible_ids,
            )
            if invalid > 0:
                # 404 + flat message (no count): a scoped caller must not be able to
                # binary-search the roster by reading how many submitted ids are out of
                # scope. Out-of-scope is indistinguishable from nonexistent (pay-group RULE).
                raise HTTPException(404, detail="Karyawan tidak ditemukan")
        async with conn.transaction():
            # Generate payroll number
            year = body.period_start.year
            month = body.period_start.month
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM payroll_runs WHERE tenant_id = $1 AND EXTRACT(YEAR FROM period_start) = $2",
                ctx["tenant_id"],
                year,
            )
            payroll_number = f"PAY-{year}-{month:02d}-{count + 1:03d}"

            uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None

            run_id = await conn.fetchval(
                """INSERT INTO payroll_runs (
                    tenant_id, payroll_number, period_start, period_end, payment_date,
                    description, status, employee_count, payment_method, bank_account_id,
                    created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, 'draft', $7, $8, $9, $10)
                RETURNING id""",
                ctx["tenant_id"],
                payroll_number,
                body.period_start,
                body.period_end,
                body.payment_date,
                body.description,
                len(body.employee_ids),
                body.payment_method,
                body.bank_account_id,
                uid,
            )

            # Persist the run roster (V283): calculate sources employees from who was
            # REQUESTED here, not from the fixed-earning slip lines seeded below (which
            # silently drop a worker paid purely by daily/hourly rate). Idempotent.
            for emp_id in body.employee_ids:
                await conn.execute(
                    "INSERT INTO payroll_run_employees (tenant_id, payroll_id, employee_id) "
                    "VALUES ($1, $2, $3) ON CONFLICT (payroll_id, employee_id) DO NOTHING",
                    ctx["tenant_id"],
                    run_id,
                    emp_id,
                )

            # Create initial slip lines from employee salary configs
            for emp_id in body.employee_ids:
                emp = await conn.fetchrow(
                    "SELECT id, name FROM employees WHERE id = $1 AND tenant_id = $2 AND is_active = true",
                    emp_id,
                    ctx["tenant_id"],
                )
                if not emp:
                    continue

                configs = await conn.fetch(
                    """SELECT esc.component_id, esc.amount, esc.percentage,
                              sc.name, sc.type, sc.category, sc.is_taxable, sc.sort_order
                       FROM employee_salary_config esc
                       JOIN salary_components sc ON sc.id = esc.component_id
                       WHERE esc.tenant_id = $1 AND esc.employee_id = $2
                         AND esc.effective_date <= $3
                         AND (esc.end_date IS NULL OR esc.end_date >= $3)
                         AND sc.type = 'earning' AND sc.is_fixed = true
                       ORDER BY sc.sort_order""",
                    ctx["tenant_id"],
                    emp_id,
                    body.period_start,
                )

                for cfg in configs:
                    await conn.execute(
                        """INSERT INTO payroll_slip_lines
                           (tenant_id, payroll_id, employee_id, component_id, component_name,
                            component_type, component_category, amount, is_taxable, sort_order)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                        ctx["tenant_id"],
                        run_id,
                        emp_id,
                        cfg["component_id"],
                        cfg["name"],
                        cfg["type"],
                        cfg["category"],
                        float(cfg["amount"]),
                        cfg["is_taxable"],
                        cfg["sort_order"],
                    )

            row = await conn.fetchrow(
                "SELECT * FROM payroll_runs WHERE id = $1", run_id
            )
            return {"success": True, "data": dict(row)}


# ========== GET ==========


@router.get("/summary")
async def payroll_summary(request: Request):
    """Dashboard payroll stats. MUST be declared BEFORE /{run_id} or the literal
    'summary' is parsed as a run_id UUID (422). Shape is the FE PayrollSummary
    (camelCase, no wrapper): {totalAmount, totalCount, breakdown:{status:{count,amount}}}.
    Same pay-group visibility as the list endpoint (runs touching accessible groups)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        role_code, accessible_ids = await _pg_filter(conn, ctx)
        conditions = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        idx = 2
        if role_code not in ("OWNER", "ADMIN"):
            if not accessible_ids:
                return {"totalAmount": 0, "totalCount": 0, "breakdown": {}}
            conditions.append(
                f"EXISTS (SELECT 1 FROM payroll_slip_lines psl JOIN employees e ON e.id = psl.employee_id WHERE psl.payroll_id = payroll_runs.id AND e.pay_group_id = ANY(${idx}::uuid[]))"
            )
            params.append(accessible_ids)
            idx += 1
        where = " AND ".join(conditions)
        rows = await conn.fetch(
            f"""SELECT status, COUNT(*) AS cnt, COALESCE(SUM(total_net_salary), 0) AS amt
                FROM payroll_runs WHERE {where} GROUP BY status""",
            *params,
        )
        breakdown = {}
        total_count = 0
        total_amount = 0.0
        for r in rows:
            breakdown[r["status"]] = {"count": r["cnt"], "amount": float(r["amt"])}
            total_count += r["cnt"]
            total_amount += float(r["amt"])
        return {
            "totalAmount": total_amount,
            "totalCount": total_count,
            "breakdown": breakdown,
        }


@router.get("/{run_id}")
async def get_payroll_run(request: Request, run_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        row = await conn.fetchrow(
            "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
            run_id,
            ctx["tenant_id"],
        )
        if not row:
            raise HTTPException(404, detail="Payroll run not found")

        # Pay group access check
        role_code, accessible_ids = await _pg_filter(conn, ctx)
        if role_code not in ("OWNER", "ADMIN"):
            if not accessible_ids:
                raise HTTPException(403, detail="No pay group access")
            visible = await conn.fetchval(
                "SELECT COUNT(*) FROM payroll_run_employees pre JOIN employees e ON e.id = pre.employee_id WHERE pre.payroll_id = $1 AND e.pay_group_id = ANY($2::uuid[])",
                run_id,
                accessible_ids,
            )
            if not visible:
                raise HTTPException(403, detail="No visible employees in this payroll")

        # Get payments summary
        payments = await conn.fetch(
            "SELECT id, payment_type, amount, status, payment_date FROM payroll_payments WHERE payroll_id = $1",
            run_id,
        )

        # Piece-line read-back (V285): the ad-hoc borongan lines stored for this run so the
        # FE edit grid can load what a full-replace PUT is about to overwrite (without this
        # an edit path silently deletes them). Pay-group scoped like /slips -- OWNER/ADMIN
        # see all; others see only lines whose employee is in an accessible pay group.
        if role_code in ("OWNER", "ADMIN"):
            piece_rows = await conn.fetch(
                """SELECT prpl.id, prpl.employee_id, prpl.description, prpl.job_reference,
                          prpl.work_order_id, prpl.quantity, prpl.rate, prpl.sort_order
                   FROM payroll_run_piece_lines prpl
                   WHERE prpl.payroll_id = $1
                   ORDER BY prpl.employee_id, prpl.sort_order""",
                run_id,
            )
        else:
            piece_rows = await conn.fetch(
                """SELECT prpl.id, prpl.employee_id, prpl.description, prpl.job_reference,
                          prpl.work_order_id, prpl.quantity, prpl.rate, prpl.sort_order
                   FROM payroll_run_piece_lines prpl
                   JOIN employees e ON e.id = prpl.employee_id
                   WHERE prpl.payroll_id = $1 AND e.pay_group_id = ANY($2::uuid[])
                   ORDER BY prpl.employee_id, prpl.sort_order""",
                run_id,
                accessible_ids,
            )
        piece_lines = [
            {
                "id": str(p["id"]),
                "employee_id": str(p["employee_id"]),
                "description": p["description"],
                "job_reference": p["job_reference"],
                "work_order_id": str(p["work_order_id"]) if p["work_order_id"] is not None else None,
                "quantity": float(p["quantity"]) if p["quantity"] is not None else None,
                "rate": float(p["rate"]) if p["rate"] is not None else None,
                "sort_order": p["sort_order"],
            }
            for p in piece_rows
        ]

        # Roster projection (payroll_run_employees) so the FE editor has rows to enter
        # hari kerja into on a fresh run -- it SURVIVES a failed calculate, unlike
        # payroll_slip_lines. Pay-group scoped exactly like /slips: OWNER/ADMIN see all;
        # a scoped caller sees only employees in an accessible pay group.
        if role_code in ("OWNER", "ADMIN"):
            roster_rows = await conn.fetch(
                """SELECT pre.employee_id, e.name AS employee_name,
                          e.employee_code, e.position
                   FROM payroll_run_employees pre
                   JOIN employees e ON e.id = pre.employee_id
                   WHERE pre.payroll_id = $1
                   ORDER BY e.name""",
                run_id,
            )
        else:
            roster_rows = await conn.fetch(
                """SELECT pre.employee_id, e.name AS employee_name,
                          e.employee_code, e.position
                   FROM payroll_run_employees pre
                   JOIN employees e ON e.id = pre.employee_id
                   WHERE pre.payroll_id = $1 AND e.pay_group_id = ANY($2::uuid[])
                   ORDER BY e.name""",
                run_id,
                accessible_ids,
            )
        employees = [
            {
                "employee_id": str(r["employee_id"]),
                "employee_name": r["employee_name"],
                "employee_code": r["employee_code"],
                "position": r["position"],
            }
            for r in roster_rows
        ]

        # Variable inputs (V282) read-back: project stored days_worked / overtime_hours /
        # amount per (employee_id, component_id) so the editor POPULATES its cells instead of
        # seeding 0 and full-replace-PUTting zeros it never read (silent earnings loss --
        # penulis-menganggap-yang-tampil-adalah-segalanya; run 82139035 lost its earnings this
        # way). A run with NO inputs returns [] (never a missing key), so the FE can distinguish
        # "read it, nothing there" from "never read it". Pay-group scoped like /slips + roster.
        if role_code in ("OWNER", "ADMIN"):
            input_rows = await conn.fetch(
                """SELECT pri.employee_id, pri.component_id,
                          pri.days_worked, pri.overtime_hours, pri.amount
                   FROM payroll_run_inputs pri
                   WHERE pri.payroll_id = $1
                   ORDER BY pri.employee_id, pri.component_id""",
                run_id,
            )
        else:
            input_rows = await conn.fetch(
                """SELECT pri.employee_id, pri.component_id,
                          pri.days_worked, pri.overtime_hours, pri.amount
                   FROM payroll_run_inputs pri
                   JOIN employees e ON e.id = pri.employee_id
                   WHERE pri.payroll_id = $1 AND e.pay_group_id = ANY($2::uuid[])
                   ORDER BY pri.employee_id, pri.component_id""",
                run_id,
                accessible_ids,
            )
        inputs = [
            {
                "employee_id": str(i["employee_id"]),
                "component_id": str(i["component_id"]) if i["component_id"] is not None else None,
                "days_worked": float(i["days_worked"]) if i["days_worked"] is not None else None,
                "overtime_hours": float(i["overtime_hours"]) if i["overtime_hours"] is not None else None,
                "amount": float(i["amount"]) if i["amount"] is not None else None,
            }
            for i in input_rows
        ]

        # Per-employee ELIGIBILITY (Finding 2): each roster employee's ASSIGNED components
        # (from the SAME ASSIGNED_COMPONENTS_SQL the engine consumes), so the FE renders
        # quantity columns PER EMPLOYEE instead of tenant-wide, and inputs for unassigned
        # components stop being silently discarded (calculate iterates assigned config only).
        # EVERY roster employee appears; one with zero assigned components returns
        # components: [] -- an explicit "not set up", NOT omitted (Rojak: "not set up" and
        # "no data" are different sentences). Scoped exactly like the roster above.
        eligibility = []
        for r in roster_rows:
            comp_rows = await conn.fetch(
                ASSIGNED_COMPONENTS_SQL, ctx["tenant_id"], r["employee_id"], row["period_start"]
            )
            eligibility.append({
                "employee_id": str(r["employee_id"]),
                "components": [
                    {
                        "component_id": str(cc["component_id"]),
                        "code": cc["code"],
                        "name": cc["name"],
                        "type": cc["type"],
                        "category": cc["category"],
                        "calculation_method": cc["calculation_method"],
                    }
                    for cc in comp_rows
                ],
            })

        return {
            "success": True,
            "data": {
                **dict(row),
                "payments": [dict(p) for p in payments],
                "piece_lines": piece_lines,
                "employees": employees,
                "inputs": inputs,
                "eligibility": eligibility,
            },
        }


# ========== UPDATE (draft only) ==========


@router.put("/{run_id}")
async def update_payroll_run(
    request: Request, run_id: UUID, body: UpdatePayrollRequest
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            run = await conn.fetchrow(
                "SELECT id, status FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
                run_id,
                ctx["tenant_id"],
            )
            if not run:
                raise HTTPException(404, detail="Payroll run not found")
            if run["status"] != "draft":
                raise HTTPException(400, detail="Can only update draft payroll runs")

            # Pay-group scope for the WRITE path (RULE: every employee_id-bearing endpoint,
            # read OR write, filters pay-group). OWNER/ADMIN keep full-replace; a scoped
            # caller may only touch employees in their accessible pay groups. Out-of-scope
            # OR nonexistent employee ids return 404 (indistinguishable from "not found") so
            # a scoped caller cannot enumerate staff outside their groups.
            role_code, accessible_ids = await _pg_filter(conn, ctx)
            privileged = role_code in ("OWNER", "ADMIN")
            scoped_emp_ids = None
            if not privileged:
                _rows = await conn.fetch(
                    "SELECT id FROM employees WHERE tenant_id = $1 AND pay_group_id = ANY($2::uuid[])",
                    ctx["tenant_id"],
                    accessible_ids,
                )
                scoped_emp_ids = {str(r["id"]) for r in _rows}

            updates = body.dict(exclude_unset=True, exclude={"variable_inputs", "piece_lines"})
            if updates:
                set_clauses = []
                params = []
                idx = 1
                for key, val in updates.items():
                    set_clauses.append(f"{key} = ${idx}")
                    params.append(val)
                    idx += 1
                params.append(run_id)
                await conn.execute(
                    f"UPDATE payroll_runs SET {', '.join(set_clauses)}, updated_at = now() WHERE id = ${idx}",
                    *params,
                )

            # Per-run variable inputs (V282 quantity x rate): store days_worked /
            # overtime_hours / amount-override per (run, employee, component). These are
            # NOT written to the GL here -- calculate_payroll multiplies them by the
            # employee's configured rate and rebuilds the slip lines. UPSERT so editing an
            # input replaces it (idempotent per component), never appends a duplicate.
            if body.variable_inputs:
                for vi in body.variable_inputs:
                    if not privileged and str(vi.employee_id) not in scoped_emp_ids:
                        raise HTTPException(404, detail="Employee not found")
                    comp = await conn.fetchrow(
                        "SELECT id FROM salary_components WHERE tenant_id = $1 AND code = $2 AND is_active = true",
                        ctx["tenant_id"],
                        vi.component_code,
                    )
                    if not comp:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "UNKNOWN_COMPONENT",
                                "message": f"Komponen gaji '{vi.component_code}' tidak ditemukan.",
                            },
                        )
                    await conn.execute(
                        """INSERT INTO payroll_run_inputs
                             (tenant_id, payroll_id, employee_id, component_id,
                              days_worked, overtime_hours, amount)
                           VALUES ($1, $2, $3, $4, $5, $6, $7)
                           ON CONFLICT (payroll_id, employee_id, component_id)
                           DO UPDATE SET days_worked = EXCLUDED.days_worked,
                                         overtime_hours = EXCLUDED.overtime_hours,
                                         amount = EXCLUDED.amount,
                                         updated_at = now()""",
                        ctx["tenant_id"],
                        run_id,
                        vi.employee_id,
                        comp["id"],
                        vi.days_worked,
                        vi.overtime_hours,
                        vi.amount,
                    )

            # Borongan piece lines (V285): full-replace the run's ad-hoc lines (FE sends the
            # complete set; [] clears them).
            if body.piece_lines is not None:
                if privileged:
                    await conn.execute(
                        "DELETE FROM payroll_run_piece_lines WHERE payroll_id = $1", run_id
                    )
                else:
                    # Reject out-of-scope / unknown employees BEFORE deleting anything (404,
                    # indistinguishable from nonexistent) so a scoped writer can neither
                    # INJECT lines for nor learn about employees outside their pay groups.
                    for pl in body.piece_lines:
                        if str(pl.employee_id) not in scoped_emp_ids:
                            raise HTTPException(404, detail="Employee not found")
                    # Full-replace only the caller's in-scope slice; other pay groups'
                    # lines are left untouched (they were never shown to this caller).
                    await conn.execute(
                        "DELETE FROM payroll_run_piece_lines WHERE payroll_id = $1 "
                        "AND employee_id IN (SELECT id FROM employees "
                        "WHERE tenant_id = $2 AND pay_group_id = ANY($3::uuid[]))",
                        run_id,
                        ctx["tenant_id"],
                        accessible_ids,
                    )
                for pl in body.piece_lines:
                    await conn.execute(
                        """INSERT INTO payroll_run_piece_lines
                             (tenant_id, payroll_id, employee_id, description, job_reference,
                              work_order_id, quantity, rate, sort_order)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                        ctx["tenant_id"],
                        run_id,
                        pl.employee_id,
                        pl.description,
                        pl.job_reference,
                        pl.work_order_id,
                        pl.quantity,
                        pl.rate,
                        pl.sort_order,
                    )

            return {"success": True, "message": "Updated"}


# ========== CALCULATE ==========


@router.post("/{run_id}/calculate")
async def calculate_payroll(request: Request, run_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        run = await conn.fetchrow(
            "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
            run_id,
            ctx["tenant_id"],
        )
        if not run:
            raise HTTPException(404, detail="Payroll run not found")
        if run["status"] not in ("draft",):
            raise HTTPException(400, detail="Can only calculate draft payroll runs")

        period_start = run["period_start"]
        period_end = run["period_end"]
        period_month = period_start.month

        # Get BPJS config
        bpjs_cfg = await get_bpjs_config(conn, ctx["tenant_id"])

        # Get all employees in this run (from existing slip lines)
        # V283: source the roster from payroll_run_employees (who was requested), NOT
        # from the seeded slip lines -- otherwise a daily/hourly-only worker with no fixed
        # earning is dropped. Fall back to slip lines for legacy runs not covered by the
        # V283 backfill.
        emp_ids = await conn.fetch(
            "SELECT employee_id FROM payroll_run_employees WHERE payroll_id = $1 ORDER BY created_at",
            run_id,
        )
        if not emp_ids:
            emp_ids = await conn.fetch(
                "SELECT DISTINCT employee_id FROM payroll_slip_lines WHERE payroll_id = $1",
                run_id,
            )
        if not emp_ids:
            raise HTTPException(400, detail="No employees in this payroll run")

        async with conn.transaction():
            # Clear existing slip lines
            await conn.execute(
                "DELETE FROM payroll_slip_lines WHERE payroll_id = $1", run_id
            )

            # Get all salary components for this tenant
            components = await conn.fetch(
                "SELECT * FROM salary_components WHERE tenant_id = $1 AND is_active = true",
                ctx["tenant_id"],
            )
            components_map = {str(c["id"]): dict(c) for c in components}

            total_gross = Decimal("0")
            total_deductions = Decimal("0")
            total_net = Decimal("0")
            total_basic = Decimal("0")  # noqa: F841 - dead-code router
            total_allowances = Decimal("0")  # noqa: F841 - dead-code router
            emp_count = 0

            results = []
            empty_employees = []  # roster members that produced no slip lines (fail loud)

            for emp_row in emp_ids:
                emp_id = emp_row["employee_id"]

                employee = await conn.fetchrow(
                    "SELECT * FROM employees WHERE id = $1 AND tenant_id = $2",
                    emp_id,
                    ctx["tenant_id"],
                )
                if not employee:
                    empty_employees.append((str(emp_id), "(karyawan tidak ditemukan)"))
                    continue

                # V283/Finding-2: shared ASSIGNED_COMPONENTS_SQL (same set the GET /{id}
                # eligibility projection returns). calculate reads component_id/amount/
                # percentage; the extra sc.* columns are ignored here.
                salary_config = await conn.fetch(
                    ASSIGNED_COMPONENTS_SQL,
                    ctx["tenant_id"],
                    emp_id,
                    period_start,
                )

                # Borongan piece lines (V285): ad-hoc {description, job_ref, qty, rate} per run.
                piece_lines = await conn.fetch(
                    """SELECT description, job_reference, work_order_id, quantity, rate
                       FROM payroll_run_piece_lines
                       WHERE payroll_id = $1 AND employee_id = $2 ORDER BY sort_order""",
                    run_id,
                    emp_id,
                )

                # V283: an employee with ZERO assigned salary components must be NAMED, not
                # silently dropped and not given a synthetic 0/employer-only line. (The engine
                # can emit employer BPJS lines even with no earnings, so "produced no lines" is
                # not the right test -- emptiness of the assigned config is.) This is the state
                # all grapgrap employees are in today.
                if not salary_config and not piece_lines:
                    empty_employees.append((str(emp_id), employee["name"]))
                    continue

                # Build per-run variable inputs (V282): days_worked / overtime_hours /
                # amount-override per component, keyed by component_id for the calc engine.
                run_inputs = await conn.fetch(
                    """SELECT component_id, days_worked, overtime_hours, amount
                       FROM payroll_run_inputs
                       WHERE payroll_id = $1 AND employee_id = $2""",
                    run_id,
                    emp_id,
                )
                variable_inputs = {
                    str(ri["component_id"]): {
                        "days_worked": ri["days_worked"],
                        "overtime_hours": ri["overtime_hours"],
                        "amount": ri["amount"],
                    }
                    for ri in run_inputs
                }

                # Get YTD data for December true-up
                ytd = None
                if period_month == 12:
                    ytd = await get_ytd_data(
                        conn,
                        ctx["tenant_id"],
                        str(emp_id),
                        period_start.year,
                        period_month,
                    )

                try:
                    slip = await calculate_employee_slip(
                        conn,
                        ctx["tenant_id"],
                        dict(employee),
                        [dict(c) for c in salary_config],
                        components_map,
                        bpjs_cfg,
                        period_start,
                        period_end,
                        period_month,
                        variable_inputs,
                        ytd,
                        [dict(p) for p in piece_lines],
                    )
                except PayrollInputError as e:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "PAYROLL_INPUT_MISSING",
                            "message": str(e),
                            "employee_id": str(emp_id),
                        },
                    )

                # Insert slip lines
                all_lines = (
                    slip["earnings"] + slip["deductions"] + slip["employer_costs"]
                )
                if not all_lines:
                    # No component produced a line (no assigned salary components, or the
                    # employee is entirely outside the period). Name them rather than drop.
                    empty_employees.append((str(emp_id), employee["name"]))
                    continue
                for line in all_lines:
                    await conn.execute(
                        """INSERT INTO payroll_slip_lines
                           (tenant_id, payroll_id, employee_id, component_id, component_name,
                            component_type, component_category, amount, quantity, rate,
                            is_taxable, sort_order, job_reference, work_order_id)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)""",
                        ctx["tenant_id"],
                        run_id,
                        emp_id,
                        UUID(line["component_id"])
                        if line.get("component_id")
                        else None,
                        line["component_name"],
                        line["component_type"],
                        line.get("component_category", ""),
                        line["amount"],
                        line.get("quantity"),
                        line.get("rate"),
                        line.get("is_taxable", False),
                        line.get("sort_order", 0),
                        line.get("job_reference"),
                        line.get("work_order_id"),
                    )

                total_gross += Decimal(str(slip["gross"]))
                total_deductions += Decimal(str(slip["total_deductions"]))
                total_net += Decimal(str(slip["net"]))
                emp_count += 1
                results.append(slip)

            if empty_employees:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "PAYROLL_EMPTY_EMPLOYEES",
                        "message": (
                            "Karyawan berikut belum menghasilkan baris gaji (belum ada "
                            "komponen gaji yang ditetapkan, atau di luar periode). Tetapkan "
                            "komponen gaji atau keluarkan dari payroll ini: "
                            + ", ".join(n for _, n in empty_employees)
                            + "."
                        ),
                        "employees": [
                            {"employee_id": i, "name": n} for i, n in empty_employees
                        ],
                    },
                )

            # Update run totals
            await conn.execute(
                """UPDATE payroll_runs SET
                   total_basic_salary = $2, total_allowances = $3,
                   total_deductions = $4, total_net_salary = $5,
                   employee_count = $6, updated_at = now()
                   WHERE id = $1""",
                run_id,
                float(total_gross),
                0,
                float(total_deductions),
                float(total_net),
                emp_count,
            )

        return {
            "success": True,
            "message": f"Calculated for {emp_count} employees",
            "data": {
                "employee_count": emp_count,
                "total_gross": float(total_gross),
                "total_deductions": float(total_deductions),
                "total_net": float(total_net),
                "slips": results,
            },
        }


# ========== WORKFLOW: SUBMIT ==========


@router.post("/{run_id}/submit")
async def submit_payroll(request: Request, run_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        run = await conn.fetchrow(
            "SELECT id, status FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
            run_id,
            ctx["tenant_id"],
        )
        if not run:
            raise HTTPException(404, detail="Payroll run not found")
        if run["status"] != "draft":
            raise HTTPException(400, detail="Can only submit draft payroll runs")

        uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
        await conn.execute(
            "UPDATE payroll_runs SET status = 'pending_approval', submitted_at = now(), submitted_by = $2 WHERE id = $1",
            run_id,
            uid,
        )
        return {"success": True, "message": "Submitted for approval"}


# ========== WORKFLOW: APPROVE ==========


@router.post("/{run_id}/approve")
async def approve_payroll(request: Request, run_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        run = await conn.fetchrow(
            "SELECT id, status FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
            run_id,
            ctx["tenant_id"],
        )
        if not run:
            raise HTTPException(404, detail="Payroll run not found")
        if run["status"] != "pending_approval":
            raise HTTPException(400, detail="Can only approve pending payroll runs")

        uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
        await conn.execute(
            "UPDATE payroll_runs SET status = 'approved', approved_at = now(), approved_by = $2 WHERE id = $1",
            run_id,
            uid,
        )
        return {"success": True, "message": "Approved"}


# ========== WORKFLOW: REJECT ==========


@router.post("/{run_id}/reject")
async def reject_payroll(
    request: Request, run_id: UUID, body: RejectPayrollRequest = None
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        run = await conn.fetchrow(
            "SELECT id, status FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
            run_id,
            ctx["tenant_id"],
        )
        if not run:
            raise HTTPException(404, detail="Payroll run not found")
        if run["status"] != "pending_approval":
            raise HTTPException(400, detail="Can only reject pending payroll runs")

        uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
        reason = body.reason if body else None
        await conn.execute(
            """UPDATE payroll_runs SET status = 'draft',
               rejected_at = now(), rejected_by = $2, rejection_reason = $3
               WHERE id = $1""",
            run_id,
            uid,
            reason,
        )
        return {"success": True, "message": "Rejected, returned to draft"}


# ========== WORKFLOW: POST (Journal creation) ==========


@router.post("/{run_id}/post")
async def post_payroll(request: Request, run_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", f"PAYROLL:{run_id}"
            )

            run = await conn.fetchrow(
                "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
                run_id,
                ctx["tenant_id"],
            )
            if not run:
                raise HTTPException(404, detail="Payroll run not found")
            if run["status"] != "approved":
                raise HTTPException(400, detail="Can only post approved payroll runs")

            # Aggregate slip lines for journal
            slip_agg = await conn.fetch(
                """SELECT component_type, component_category,
                          SUM(amount) as total
                   FROM payroll_slip_lines
                   WHERE payroll_id = $1
                   GROUP BY component_type, component_category""",
                run_id,
            )

            # Refuse to post a run that would produce a ZERO-LINE journal (the FE-showable
            # half; the V287 constraint trigger is the universal DB backstop). Every line
            # insert below is gated on amount > 0, so with no slip rows the journal gets
            # zero lines yet still flips to POSTED -- exactly how JV-PAY-PAY-2026-09-012
            # (0 lines, total 0) was produced live on 2026-09-22.
            if not slip_agg:
                raise HTTPException(
                    400,
                    detail="Payroll ini belum punya slip gaji (0 baris) -- jalankan Hitung (Calculate) dulu sebelum posting ke jurnal.",
                )

            total_earnings = sum(
                r["total"] for r in slip_agg if r["component_type"] == "earning"
            )
            total_bpjs_er = sum(
                r["total"]
                for r in slip_agg
                if r["component_type"] == "employer_cost"
                and r["component_category"] not in ("pph21_employer",)
            )
            total_pph21_er = sum(
                r["total"]
                for r in slip_agg
                if r["component_category"] == "pph21_employer"
            )
            total_pph21_ee = sum(
                r["total"] for r in slip_agg if r["component_category"] == "pph21"
            )
            total_bpjs_ee = sum(
                r["total"]
                for r in slip_agg
                if r["component_type"] == "deduction"
                and r["component_category"].startswith("bpjs_")
            )
            total_kasbon = sum(
                r["total"] for r in slip_agg if r["component_category"] == "kasbon"
            )
            total_net = total_earnings - total_pph21_ee - total_bpjs_ee - total_kasbon

            # Resolve CoA accounts via role catalog (Law 27, Fase D4.3).
            # PPH21_PAYABLE -> 2-10310 (payroll-exclusive boundary), NOT
            # the generic 2-10300 the legacy literal used to point at.
            coa_beban_gaji = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.SALARY_EXPENSE
            )
            coa_beban_bpjs = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.BPJS_ER_EXPENSE
            )
            coa_hutang_gaji = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.SALARY_PAYABLE
            )
            coa_hutang_pph = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.PPH21_PAYABLE
            )
            coa_hutang_bpjs_ee = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.BPJS_EE_PAYABLE
            )
            coa_hutang_bpjs_er = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.BPJS_ER_PAYABLE
            )

            # V286: group EARNINGS by destination account (per-component expense_role;
            # borongan -> PRODUCTION_WAGE_EXPENSE; NULL -> SALARY_EXPENSE default).
            _earn_rows = await conn.fetch(
                """SELECT COALESCE(sc.expense_role,
                            CASE WHEN psl.component_category='borongan'
                                 THEN 'PRODUCTION_WAGE_EXPENSE' ELSE NULL END) AS role,
                          SUM(psl.amount) AS total
                   FROM payroll_slip_lines psl
                   LEFT JOIN salary_components sc ON sc.id = psl.component_id
                   WHERE psl.payroll_id = $1 AND psl.component_type = 'earning'
                   GROUP BY 1""",
                run_id,
            )
            earnings_by_account = {}
            for _r in _earn_rows:
                _acct = await _resolve_wage_account(conn, ctx["tenant_id"], _r["role"])
                earnings_by_account[_acct] = earnings_by_account.get(_acct, Decimal("0")) + _r["total"]

            # V286: employer costs (BPJS employer etc.) INHERIT the employee's PRIMARY earning
            # destination when that is a configured production role; otherwise stay on
            # BPJS_ER_EXPENSE (so an unconfigured tenant is unchanged). Rule: primary = the
            # employee's single largest earning line's role.
            _prim_rows = await conn.fetch(
                """SELECT employee_id, role FROM (
                       SELECT psl.employee_id,
                              COALESCE(sc.expense_role,
                                  CASE WHEN psl.component_category='borongan'
                                       THEN 'PRODUCTION_WAGE_EXPENSE' ELSE NULL END) AS role,
                              ROW_NUMBER() OVER (PARTITION BY psl.employee_id
                                  ORDER BY SUM(psl.amount) DESC) AS rn
                       FROM payroll_slip_lines psl
                       LEFT JOIN salary_components sc ON sc.id = psl.component_id
                       WHERE psl.payroll_id = $1 AND psl.component_type = 'earning'
                       GROUP BY psl.employee_id, 2
                   ) x WHERE rn = 1""",
                run_id,
            )
            _primary_role = {r["employee_id"]: r["role"] for r in _prim_rows}
            _emp_cost_rows = await conn.fetch(
                """SELECT employee_id, SUM(amount) AS total FROM payroll_slip_lines
                   WHERE payroll_id = $1 AND component_type = 'employer_cost'
                     AND component_category != 'pph21_employer'
                   GROUP BY employee_id""",
                run_id,
            )
            employer_by_account = {}
            for _r in _emp_cost_rows:
                _pr = _primary_role.get(_r["employee_id"])
                if _pr and _pr != "SALARY_EXPENSE":
                    _acct = await _resolve_wage_account(conn, ctx["tenant_id"], _pr)
                else:
                    _acct = coa_beban_bpjs
                employer_by_account[_acct] = employer_by_account.get(_acct, Decimal("0")) + _r["total"]

            total_debit = total_earnings + total_bpjs_er
            total_credit = total_debit  # balanced

            if total_pph21_er > 0:
                coa_beban_pph = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.PPH21_ER_EXPENSE
                )
                total_debit += total_pph21_er
                total_credit += total_pph21_er

            # Law 20: DRAFT journal
            journal_number = f"JV-PAY-{run['payroll_number']}"
            journal_id = await conn.fetchval(
                """INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit
                ) VALUES ($1, $2, $3, $4, 'PAYROLL', $5, 'DRAFT', $6, $7)
                RETURNING id""",
                ctx["tenant_id"],
                journal_number,
                run["period_end"],
                f"Payroll: {run['payroll_number']} ({run['period_start']} - {run['period_end']})",
                str(run_id),
                float(total_debit),
                float(total_credit),
            )

            line_num = 1

            # Dr earnings, grouped by destination account (V286: production wage -> COGS,
            # office/admin -> Beban Gaji). Sum across groups == total_earnings, so the header balances.
            for _acct, _amt in earnings_by_account.items():
                if _amt and _amt > 0:
                    await conn.execute(
                        """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                           VALUES ($1, $2, $3, $4, 0, 'Beban Gaji/Upah & Tunjangan')""",
                        str(journal_id), line_num, _acct, float(_amt),
                    )
                    line_num += 1

            # Dr employer costs, following each employee's primary wage destination (V286).
            for _acct, _amt in employer_by_account.items():
                if _amt and _amt > 0:
                    await conn.execute(
                        """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                           VALUES ($1, $2, $3, $4, 0, 'Beban BPJS Perusahaan')""",
                        str(journal_id), line_num, _acct, float(_amt),
                    )
                    line_num += 1

            # Dr Beban PPh 21 Perusahaan (nett method only)
            if total_pph21_er > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, $4, 0, 'Beban PPh 21 Perusahaan')""",
                    str(journal_id),
                    line_num,
                    coa_beban_pph,
                    float(total_pph21_er),
                )
                line_num += 1

            # Cr Hutang Gaji
            if total_net > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, 0, $4, 'Hutang Gaji')""",
                    str(journal_id),
                    line_num,
                    coa_hutang_gaji,
                    float(total_net),
                )
                line_num += 1

            # Cr Hutang PPh 21
            total_pph21 = total_pph21_ee + total_pph21_er
            if total_pph21 > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, 0, $4, 'Hutang PPh 21')""",
                    str(journal_id),
                    line_num,
                    coa_hutang_pph,
                    float(total_pph21),
                )
                line_num += 1

            # Cr Hutang BPJS Karyawan
            if total_bpjs_ee > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, 0, $4, 'Hutang BPJS Karyawan')""",
                    str(journal_id),
                    line_num,
                    coa_hutang_bpjs_ee,
                    float(total_bpjs_ee),
                )
                line_num += 1

            # Cr Hutang BPJS Perusahaan
            if total_bpjs_er > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, 0, $4, 'Hutang BPJS Perusahaan')""",
                    str(journal_id),
                    line_num,
                    coa_hutang_bpjs_er,
                    float(total_bpjs_er),
                )
                line_num += 1

            # ---- KASBON: route kasbon deductions (component_category='kasbon') to the
            # employee-advance ledger. Cr EMPLOYEE_ADVANCE (reduces Piutang Karyawan); net
            # was already reduced by total_kasbon above. Per employee, FIFO across active
            # advances (oldest granted_date first, cascade); an advance hitting 0 -> settled.
            # Over-deduction (kasbon > total remaining) -> 422 and the WHOLE run rolls back
            # (single transaction), so no employee is half-applied.
            if total_kasbon > 0:
                try:
                    coa_emp_adv = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.EMPLOYEE_ADVANCE
                    )
                except AccountRoleUnmappedError:
                    raise HTTPException(
                        status_code=422,
                        detail={"code": "ACCOUNT_DEFAULT_UNMAPPED",
                                "message": "Akun Piutang Karyawan (kasbon) belum diatur untuk usaha ini."},
                    )
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, 0, $4, 'Potongan Kasbon (Piutang Karyawan)')""",
                    str(journal_id), line_num, coa_emp_adv, float(total_kasbon),
                )
                line_num += 1
                kasbon_rows = await conn.fetch(
                    """SELECT employee_id, SUM(amount) AS kasbon
                       FROM payroll_slip_lines
                       WHERE payroll_id = $1 AND component_category = 'kasbon' AND amount > 0
                       GROUP BY employee_id""",
                    run_id,
                )
                for kr in kasbon_rows:
                    emp_id = kr["employee_id"]
                    left = float(kr["kasbon"])
                    advs = await conn.fetch(
                        """SELECT id, employee_advance_balance(id) AS remaining
                           FROM employee_advances
                           WHERE tenant_id = $1 AND employee_id = $2 AND status = 'active'
                           ORDER BY granted_date, created_at""",
                        ctx["tenant_id"], emp_id,
                    )
                    total_remaining = sum(float(a["remaining"]) for a in advs)
                    if left > total_remaining + 0.005:
                        emp_name = await conn.fetchval(
                            "SELECT name FROM employees WHERE id = $1", emp_id
                        ) or str(emp_id)
                        raise HTTPException(
                            status_code=422,
                            detail={"code": "KASBON_OVER_DEDUCTION",
                                    "message": (
                                        f"Potongan kasbon {left:,.0f} melebihi sisa kasbon "
                                        f"{total_remaining:,.0f} untuk {emp_name}."
                                    ).replace(",", ".")},
                        )
                    for a in advs:
                        if left <= 0.005:
                            break
                        cut = min(float(a["remaining"]), left)
                        if cut <= 0:
                            continue
                        await conn.execute(
                            """INSERT INTO employee_advance_movements
                                   (tenant_id, advance_id, employee_id, movement_type, amount, payroll_id, journal_id, created_by)
                               VALUES ($1, $2, $3, 'deduction', $4, $5, $6, $7)""",
                            ctx["tenant_id"], a["id"], emp_id, -cut, run_id, journal_id,
                            (UUID(ctx["user_id"]) if ctx.get("user_id") else None),
                        )
                        left -= cut
                        newbal = float(await conn.fetchval("SELECT employee_advance_balance($1)", a["id"]))
                        if abs(newbal) < 0.005:
                            await conn.execute(
                                "UPDATE employee_advances SET status = 'settled' WHERE id = $1", a["id"]
                            )

            # Law 20: DRAFT -> POSTED
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )

            # Update payroll run status
            uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
            await conn.execute(
                """UPDATE payroll_runs SET status = 'posted',
                   journal_id = $2, posted_at = now(), posted_by = $3
                   WHERE id = $1""",
                run_id,
                journal_id,
                uid,
            )

            # Create withholding_tax_records for PPh 21
            pph_slips = await conn.fetch(
                """SELECT employee_id, SUM(amount) as pph_amount
                   FROM payroll_slip_lines
                   WHERE payroll_id = $1 AND component_category = 'pph21' AND amount > 0
                   GROUP BY employee_id""",
                run_id,
            )

            for ps in pph_slips:
                # Get tax_code for PPh 21
                tax_code = await conn.fetchrow(
                    "SELECT id FROM tax_codes WHERE tenant_id = $1 AND tax_type = 'pph21' AND is_active = true LIMIT 1",
                    ctx["tenant_id"],
                )
                if tax_code:
                    emp_gross = await conn.fetchval(
                        """SELECT SUM(amount) FROM payroll_slip_lines
                           WHERE payroll_id = $1 AND employee_id = $2 AND component_type = 'earning'""",
                        run_id,
                        ps["employee_id"],
                    )
                    # Wave 4: Fetch employee NPWP for WHT record
                    emp_npwp = await conn.fetchval(
                        "SELECT npwp FROM employees WHERE id = $1 AND tenant_id = $2",
                        ps["employee_id"],
                        ctx["tenant_id"],
                    )
                    reporting_period = (
                        f"{run['period_start'].year}-{run['period_start'].month:02d}"
                    )
                    await conn.execute(
                        """INSERT INTO withholding_tax_records
                           (tenant_id, direction, tax_code_id, document_type, document_id,
                            journal_id, tax_period, base_amount, tax_amount, status, npwp)
                           VALUES ($1, 'cut', $2, 'PAYROLL', $3, $4, $5, $6, $7, 'recorded', $8)""",
                        ctx["tenant_id"],
                        tax_code["id"],
                        run_id,
                        journal_id,
                        reporting_period,
                        float(emp_gross or 0),
                        float(ps["pph_amount"]),
                        emp_npwp,
                    )

        return {"success": True, "message": "Posted", "journal_id": str(journal_id)}


# ========== WORKFLOW: VOID ==========


@router.post("/{run_id}/void")
async def void_payroll(request: Request, run_id: UUID, body: VoidPayrollRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        run = await conn.fetchrow(
            "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
            run_id,
            ctx["tenant_id"],
        )
        if not run:
            raise HTTPException(404, detail="Payroll run not found")
        if run["status"] != "posted":
            raise HTTPException(400, detail="Can only void posted payroll runs")

        # VOID GUARD: check no posted payments
        active_payments = await conn.fetch(
            "SELECT id, payment_type, status FROM payroll_payments WHERE payroll_id = $1 AND status = 'posted'",
            run_id,
        )
        if active_payments:
            raise HTTPException(
                400,
                detail={
                    "error": "VOID_BLOCKED_BY_PAYMENTS",
                    "message": f"Void gagal: {len(active_payments)} payment masih posted. Void semua payment terlebih dahulu.",
                    "active_payments": [dict(p) for p in active_payments],
                },
            )

        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", f"PAYROLL:{run_id}"
            )

            # Create reversal journal
            if run["journal_id"]:
                orig = await conn.fetchrow(
                    "SELECT * FROM journal_entries WHERE id = $1", run["journal_id"]
                )
                if orig:
                    rev_id = await conn.fetchval(
                        """INSERT INTO journal_entries (
                            tenant_id, journal_number, journal_date, description,
                            source_type, source_id, status, total_debit, total_credit,
                            reversal_of_id
                        ) VALUES ($1, $2, CURRENT_DATE, $3, 'PAYROLL', $4, 'DRAFT', $5, $6, $7)
                        RETURNING id""",
                        ctx["tenant_id"],
                        f"REV-{orig['journal_number']}",
                        f"Reversal: {orig['description']}",
                        str(run_id),
                        float(orig["total_debit"]),
                        float(orig["total_credit"]),
                        orig["id"],
                    )

                    orig_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1",
                        str(orig["id"]),
                    )
                    for ol in orig_lines:
                        await conn.execute(
                            """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                               VALUES ($1, $2, $3, $4, $5, $6)""",
                            str(rev_id),
                            ol["line_number"],
                            ol["account_id"],
                            float(ol["credit"]),
                            float(ol["debit"]),
                            f"Reversal: {ol['memo'] or ''}",
                        )

                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        rev_id,
                    )
                    await conn.execute(
                        "UPDATE journal_entries SET reversed_by_id = $1 WHERE id = $2",
                        rev_id,
                        orig["id"],
                    )

                    # Kasbon: reverse this run's deduction movements (+amount restores the
                    # derived balance) and un-settle any advance the run had auto-settled.
                    _dmovs = await conn.fetch(
                        """SELECT id, advance_id, employee_id, amount
                           FROM employee_advance_movements
                           WHERE payroll_id = $1 AND movement_type = 'deduction'""",
                        run_id,
                    )
                    _ruid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
                    for _dm in _dmovs:
                        await conn.execute(
                            """INSERT INTO employee_advance_movements
                                   (tenant_id, advance_id, employee_id, movement_type, amount, payroll_id, journal_id, reverses_movement_id, created_by)
                               VALUES ($1, $2, $3, 'reversal', $4, $5, $6, $7, $8)""",
                            ctx["tenant_id"], _dm["advance_id"], _dm["employee_id"],
                            -float(_dm["amount"]), run_id, rev_id, _dm["id"], _ruid,
                        )
                    for _aid in {_dm["advance_id"] for _dm in _dmovs}:
                        _bal = float(await conn.fetchval("SELECT employee_advance_balance($1)", _aid))
                        if abs(_bal) > 0.005:
                            await conn.execute(
                                "UPDATE employee_advances SET status = 'active' WHERE id = $1 AND status = 'settled'", _aid
                            )

            uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
            await conn.execute(
                """UPDATE payroll_runs SET status = 'voided',
                   voided_at = now(), voided_by = $2, void_reason = $3
                   WHERE id = $1""",
                run_id,
                uid,
                body.reason,
            )

        return {"success": True, "message": "Voided"}


# ========== SLIPS ==========


@router.get("/{run_id}/slips")
async def get_slips(request: Request, run_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        role_code, accessible_ids = await _pg_filter(conn, ctx)
        if role_code not in ("OWNER", "ADMIN") and accessible_ids:
            lines = await conn.fetch(
                """SELECT psl.*, e.name as employee_name, e.employee_code,
                          e.position, e.department, e.npwp, e.nik,
                          sc.calculation_method
                   FROM payroll_slip_lines psl
                   JOIN employees e ON e.id = psl.employee_id
                   LEFT JOIN salary_components sc ON sc.id = psl.component_id
                   WHERE psl.payroll_id = $1 AND e.pay_group_id = ANY($2::uuid[])
                   ORDER BY e.name, psl.sort_order""",
                run_id,
                accessible_ids,
            )
        elif role_code not in ("OWNER", "ADMIN"):
            return {"success": True, "data": []}
        else:
            lines = await conn.fetch(
                """SELECT psl.*, e.name as employee_name, e.employee_code,
                          e.position, e.department, e.npwp, e.nik,
                          sc.calculation_method
                   FROM payroll_slip_lines psl
                   JOIN employees e ON e.id = psl.employee_id
                   LEFT JOIN salary_components sc ON sc.id = psl.component_id
                   WHERE psl.payroll_id = $1
                   ORDER BY e.name, psl.sort_order""",
                run_id,
            )

        # Group by employee
        slips = {}
        for line in lines:
            eid = str(line["employee_id"])
            if eid not in slips:
                slips[eid] = {
                    "employee_id": eid,
                    "employee_name": line["employee_name"],
                    "employee_code": line["employee_code"],
                    "position": line["position"],
                    "department": line["department"],
                    "npwp": line["npwp"],
                    "nik": line["nik"],
                    "earnings": [],
                    "deductions": [],
                    "employer_costs": [],
                    "gross": 0,
                    "total_deductions": 0,
                    "net": 0,
                }
            entry = {
                "component_name": line["component_name"],
                "component_category": line["component_category"],
                "component_type": line["component_type"],
                "amount": float(line["amount"]),
                # V282/V285: qty x rate + borongan piece detail. NULL for flat/fixed lines.
                "quantity": float(line["quantity"]) if line["quantity"] is not None else None,
                "rate": float(line["rate"]) if line["rate"] is not None else None,
                "job_reference": line["job_reference"],
                "work_order_id": str(line["work_order_id"]) if line["work_order_id"] is not None else None,
                # unit hint: 'daily'->hari, 'hourly'/'overtime'->jam, else/NULL (borongan/flat)->bare x
                "calculation_method": line["calculation_method"],
            }
            if line["component_type"] == "earning":
                slips[eid]["earnings"].append(entry)
                slips[eid]["gross"] += float(line["amount"])
            elif line["component_type"] == "deduction":
                slips[eid]["deductions"].append(entry)
                slips[eid]["total_deductions"] += float(line["amount"])
            elif line["component_type"] == "employer_cost":
                slips[eid]["employer_costs"].append(entry)

        for s in slips.values():
            s["net"] = s["gross"] - s["total_deductions"]

        return {"success": True, "data": list(slips.values())}
