"""
Payroll Config Router — BPJS rates + Monthly Recap
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from datetime import date as date_type
import logging
import asyncpg

from ..schemas.payroll import UpdateBpjsConfigRequest
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


@router.get("/bpjs")
async def get_bpjs_config(request: Request):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        rows = await conn.fetch(
            """SELECT DISTINCT ON (component) *
               FROM bpjs_config WHERE tenant_id = $1 AND is_active = true
               ORDER BY component, effective_date DESC""",
            ctx["tenant_id"],
        )
        return {"success": True, "data": [dict(r) for r in rows]}


@router.put("/bpjs")
async def update_bpjs_config(request: Request, body: UpdateBpjsConfigRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        async with conn.transaction():
            for cfg in body.configs:
                await conn.execute(
                    """INSERT INTO bpjs_config
                       (tenant_id, component, employer_rate, employee_rate, ceiling_amount, effective_date)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (tenant_id, component, effective_date)
                       DO UPDATE SET employer_rate = $3, employee_rate = $4, ceiling_amount = $5""",
                    ctx["tenant_id"],
                    cfg["component"],
                    cfg["employer_rate"],
                    cfg["employee_rate"],
                    cfg.get("ceiling_amount"),
                    date_type.fromisoformat(cfg["effective_date"])
                    if isinstance(cfg["effective_date"], str)
                    else cfg["effective_date"],
                )
        return {"success": True, "message": "BPJS config updated"}


@router.get("/reports/monthly-recap")
async def monthly_recap(
    request: Request,
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        run = await conn.fetchrow(
            """SELECT id, payroll_number, period_start, period_end, status,
                      total_basic_salary, total_net_salary, employee_count
               FROM payroll_runs
               WHERE tenant_id = $1 AND EXTRACT(YEAR FROM period_start) = $2
                 AND EXTRACT(MONTH FROM period_start) = $3 AND status = 'posted'
               LIMIT 1""",
            ctx["tenant_id"],
            year,
            month,
        )
        if not run:
            return {
                "success": True,
                "data": None,
                "message": "No posted payroll for this period",
            }

        # Per-employee breakdown
        employees = await conn.fetch(
            """SELECT e.name, e.employee_code, e.position, e.department,
                      SUM(CASE WHEN psl.component_type = 'earning' THEN psl.amount ELSE 0 END) as gross,
                      SUM(CASE WHEN psl.component_type = 'deduction' THEN psl.amount ELSE 0 END) as deductions,
                      SUM(CASE WHEN psl.component_category = 'pph21' THEN psl.amount ELSE 0 END) as pph21,
                      SUM(CASE WHEN psl.component_type = 'employer_cost' THEN psl.amount ELSE 0 END) as employer_cost
               FROM payroll_slip_lines psl
               JOIN employees e ON e.id = psl.employee_id
               WHERE psl.payroll_id = $1
               GROUP BY e.id, e.name, e.employee_code, e.position, e.department
               ORDER BY e.name""",
            run["id"],
        )

        # Totals
        totals = await conn.fetchrow(
            """SELECT
                  SUM(CASE WHEN component_type = 'earning' THEN amount ELSE 0 END) as total_gross,
                  SUM(CASE WHEN component_type = 'deduction' THEN amount ELSE 0 END) as total_deductions,
                  SUM(CASE WHEN component_category = 'pph21' THEN amount ELSE 0 END) as total_pph21,
                  SUM(CASE WHEN component_type = 'employer_cost' THEN amount ELSE 0 END) as total_employer_cost
               FROM payroll_slip_lines WHERE payroll_id = $1""",
            run["id"],
        )

        return {
            "success": True,
            "data": {
                "payroll_number": run["payroll_number"],
                "period": f"{year}-{month:02d}",
                "employee_count": run["employee_count"],
                "employees": [dict(e) for e in employees],
                "totals": dict(totals) if totals else {},
            },
        }
