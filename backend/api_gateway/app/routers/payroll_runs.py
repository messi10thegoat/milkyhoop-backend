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
    COA_BEBAN_GAJI,
    COA_BEBAN_BPJS_ER,
    COA_BEBAN_PPH21_ER,
    COA_HUTANG_GAJI,
    COA_HUTANG_PPH21,
    COA_HUTANG_BPJS_EE,
    COA_HUTANG_BPJS_ER,
)
from ..services.resolve_account import resolve_account_id
from ..services.pay_group_access import get_accessible_pay_group_ids, get_user_role_code
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


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
                raise HTTPException(
                    403,
                    detail=f"{invalid} employee(s) not in your accessible pay groups",
                )
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
                "SELECT COUNT(*) FROM payroll_slip_lines psl JOIN employees e ON e.id = psl.employee_id WHERE psl.payroll_id = $1 AND e.pay_group_id = ANY($2::uuid[])",
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

        return {
            "success": True,
            "data": {**dict(row), "payments": [dict(p) for p in payments]},
        }


# ========== UPDATE (draft only) ==========


@router.put("/{run_id}")
async def update_payroll_run(
    request: Request, run_id: UUID, body: UpdatePayrollRequest
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
        if run["status"] != "draft":
            raise HTTPException(400, detail="Can only update draft payroll runs")

        updates = body.dict(exclude_unset=True, exclude={"variable_inputs"})
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

        # Store variable inputs as metadata (used during calculate)
        if body.variable_inputs:
            for vi in body.variable_inputs:
                if vi.overtime_hours is not None:
                    # Store lembur hours as slip line placeholder
                    await conn.execute(
                        """INSERT INTO payroll_slip_lines
                           (tenant_id, payroll_id, employee_id, component_name,
                            component_type, component_category, amount, is_taxable, sort_order)
                           VALUES ($1, $2, $3, 'Lembur (input)', 'earning', 'lembur', $4, true, 20)
                           ON CONFLICT DO NOTHING""",
                        ctx["tenant_id"],
                        run_id,
                        vi.employee_id,
                        vi.overtime_hours or 0,
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
            total_basic = Decimal("0")
            total_allowances = Decimal("0")
            emp_count = 0

            results = []

            for emp_row in emp_ids:
                emp_id = emp_row["employee_id"]

                employee = await conn.fetchrow(
                    "SELECT * FROM employees WHERE id = $1 AND tenant_id = $2",
                    emp_id,
                    ctx["tenant_id"],
                )
                if not employee:
                    continue

                salary_config = await conn.fetch(
                    """SELECT esc.component_id, esc.amount, esc.percentage
                       FROM employee_salary_config esc
                       JOIN salary_components sc ON sc.id = esc.component_id
                       WHERE esc.tenant_id = $1 AND esc.employee_id = $2
                         AND esc.effective_date <= $3
                         AND (esc.end_date IS NULL OR esc.end_date >= $3)
                       ORDER BY sc.sort_order""",
                    ctx["tenant_id"],
                    emp_id,
                    period_start,
                )

                # Build variable inputs (for now empty, TODO: store in metadata)
                variable_inputs = {}

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
                )

                # Insert slip lines
                all_lines = (
                    slip["earnings"] + slip["deductions"] + slip["employer_costs"]
                )
                for line in all_lines:
                    await conn.execute(
                        """INSERT INTO payroll_slip_lines
                           (tenant_id, payroll_id, employee_id, component_id, component_name,
                            component_type, component_category, amount, is_taxable, sort_order)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
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
                        line.get("is_taxable", False),
                        line.get("sort_order", 0),
                    )

                total_gross += Decimal(str(slip["gross"]))
                total_deductions += Decimal(str(slip["total_deductions"]))
                total_net += Decimal(str(slip["net"]))
                emp_count += 1
                results.append(slip)

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
            total_net = total_earnings - total_pph21_ee - total_bpjs_ee

            # Resolve CoA accounts (Law 27)
            coa_beban_gaji = await resolve_account_id(
                conn, ctx["tenant_id"], COA_BEBAN_GAJI
            )
            coa_beban_bpjs = await resolve_account_id(
                conn, ctx["tenant_id"], COA_BEBAN_BPJS_ER
            )
            coa_hutang_gaji = await resolve_account_id(
                conn, ctx["tenant_id"], COA_HUTANG_GAJI
            )
            coa_hutang_pph = await resolve_account_id(
                conn, ctx["tenant_id"], COA_HUTANG_PPH21
            )
            coa_hutang_bpjs_ee = await resolve_account_id(
                conn, ctx["tenant_id"], COA_HUTANG_BPJS_EE
            )
            coa_hutang_bpjs_er = await resolve_account_id(
                conn, ctx["tenant_id"], COA_HUTANG_BPJS_ER
            )

            total_debit = total_earnings + total_bpjs_er
            total_credit = total_debit  # balanced

            if total_pph21_er > 0:
                coa_beban_pph = await resolve_account_id(
                    conn, ctx["tenant_id"], COA_BEBAN_PPH21_ER
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

            # Dr Beban Gaji & Tunjangan
            if total_earnings > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, $4, 0, 'Beban Gaji & Tunjangan')""",
                    str(journal_id),
                    line_num,
                    coa_beban_gaji,
                    float(total_earnings),
                )
                line_num += 1

            # Dr Beban BPJS Perusahaan
            if total_bpjs_er > 0:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, $4, 0, 'Beban BPJS Perusahaan')""",
                    str(journal_id),
                    line_num,
                    coa_beban_bpjs,
                    float(total_bpjs_er),
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
                          e.position, e.department, e.npwp, e.nik
                   FROM payroll_slip_lines psl
                   JOIN employees e ON e.id = psl.employee_id
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
                          e.position, e.department, e.npwp, e.nik
                   FROM payroll_slip_lines psl
                   JOIN employees e ON e.id = psl.employee_id
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
                "amount": float(line["amount"]),
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
