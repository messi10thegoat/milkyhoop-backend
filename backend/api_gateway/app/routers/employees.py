"""
Employees Router — Karyawan Master Data CRUD
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, List
from pydantic import BaseModel
from uuid import UUID
import logging
import asyncpg

from ..schemas.employees import (
    CreateEmployeeRequest,
    UpdateEmployeeRequest,
    SetSalaryConfigRequest,
)
from ..config import settings
from ..services.pay_group_access import get_accessible_pay_group_ids, get_user_role_code

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


# ========== LIST ==========


@router.get("")
async def list_employees(
    request: Request,
    search: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    employee_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        # Pay group filtering
        role_code = await get_user_role_code(
            str(ctx["user_id"]), str(ctx["tenant_id"]), conn
        )
        accessible_ids = await get_accessible_pay_group_ids(
            str(ctx["user_id"]), str(ctx["tenant_id"]), role_code, conn
        )

        conditions = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        idx = 2

        if role_code not in ("OWNER", "ADMIN"):
            if not accessible_ids:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                }
            conditions.append(f"pay_group_id = ANY(${idx}::uuid[])")
            params.append(accessible_ids)
            idx += 1

        if search:
            conditions.append(
                f"(name ILIKE ${idx} OR employee_code ILIKE ${idx} OR email ILIKE ${idx})"
            )
            params.append(f"%{search}%")
            idx += 1
        if department:
            conditions.append(f"department = ${idx}")
            params.append(department)
            idx += 1
        if employee_type:
            conditions.append(f"employee_type = ${idx}")
            params.append(employee_type)
            idx += 1
        if is_active is not None:
            conditions.append(f"is_active = ${idx}")
            params.append(is_active)
            idx += 1

        where = " AND ".join(conditions)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM employees WHERE {where}", *params
        )

        offset = (page - 1) * page_size
        rows = await conn.fetch(
            f"""SELECT id, employee_code, name, email, department, position, employee_type,
                       ptkp_status, is_active, join_date, resign_date, phone,
                       is_bpjs_kes, is_bpjs_jht, is_bpjs_jp, pay_group_id, created_at
                FROM employees WHERE {where}
                ORDER BY name ASC
                LIMIT {page_size} OFFSET {offset}""",
            *params,
        )

        return {
            "success": True,
            "data": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


# ========== CREATE ==========


@router.post("")
async def create_employee(request: Request, body: CreateEmployeeRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        # Generate employee code if not provided
        code = body.employee_code
        if not code:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM employees WHERE tenant_id = $1", ctx["tenant_id"]
            )
            code = f"EMP{count + 1:04d}"

        row = await conn.fetchrow(
            """INSERT INTO employees (
                tenant_id, employee_code, name, email, department, position,
                nik, npwp, ptkp_status, tax_method, marital_status, date_of_birth,
                gender, religion, join_date, employee_type, bpjs_kes_number,
                bpjs_tk_number, bank_name, bank_account_number, bank_account_name,
                jkk_risk_level, is_bpjs_kes, is_bpjs_jht, is_bpjs_jp, phone, address
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27
            ) RETURNING *""",
            ctx["tenant_id"],
            code,
            body.name,
            body.email,
            body.department,
            body.position,
            body.nik,
            body.npwp,
            body.ptkp_status,
            body.tax_method,
            body.marital_status,
            body.date_of_birth,
            body.gender,
            body.religion,
            body.join_date,
            body.employee_type,
            body.bpjs_kes_number,
            body.bpjs_tk_number,
            body.bank_name,
            body.bank_account_number,
            body.bank_account_name,
            body.jkk_risk_level,
            body.is_bpjs_kes,
            body.is_bpjs_jht,
            body.is_bpjs_jp,
            body.phone,
            body.address,
        )
        return {"success": True, "data": dict(row)}


# ========== BULK GROUP ASSIGNMENT ==========


class BulkAssignGroupRequest(BaseModel):
    employee_ids: List[str]
    pay_group_id: str


@router.patch("/bulk-group")
async def bulk_assign_group(request: Request, body: BulkAssignGroupRequest):
    """Bulk assign employees to a pay group. OWNER/ADMIN only."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        role_code = await get_user_role_code(
            str(ctx["user_id"]), str(ctx["tenant_id"]), conn
        )
        if role_code not in ("OWNER", "ADMIN"):
            raise HTTPException(
                status_code=403, detail="Only OWNER/ADMIN can reassign pay groups"
            )

        # Validate pay group exists
        pg = await conn.fetchrow(
            "SELECT id FROM pay_groups WHERE id = $1 AND tenant_id = $2 AND is_active = true",
            body.pay_group_id,
            ctx["tenant_id"],
        )
        if not pg:
            raise HTTPException(status_code=404, detail="Pay group not found")

        result = await conn.execute(
            """UPDATE employees SET pay_group_id = $1, updated_at = now()
               WHERE id = ANY($2::uuid[]) AND tenant_id = $3""",
            body.pay_group_id,
            body.employee_ids,
            ctx["tenant_id"],
        )
        count = int(result.split()[-1])
        return {
            "success": True,
            "message": f"{count} employee(s) updated",
            "data": {"updated": count},
        }


# ========== GET ==========


@router.get("/{employee_id}")
async def get_employee(request: Request, employee_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        row = await conn.fetchrow(
            "SELECT * FROM employees WHERE id = $1 AND tenant_id = $2",
            employee_id,
            ctx["tenant_id"],
        )
        if not row:
            raise HTTPException(404, detail="Employee not found")
        return {"success": True, "data": dict(row)}


# ========== UPDATE ==========


@router.put("/{employee_id}")
async def update_employee(
    request: Request, employee_id: UUID, body: UpdateEmployeeRequest
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM employees WHERE id = $1 AND tenant_id = $2",
            employee_id,
            ctx["tenant_id"],
        )
        if not existing:
            raise HTTPException(404, detail="Employee not found")

        updates = {}
        for field, value in body.dict(exclude_unset=True).items():
            updates[field] = value

        if not updates:
            raise HTTPException(400, detail="No fields to update")

        set_clauses = []
        params = []
        idx = 1
        for key, val in updates.items():
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
            idx += 1

        params.append(employee_id)
        params.append(ctx["tenant_id"])

        row = await conn.fetchrow(
            f"""UPDATE employees SET {', '.join(set_clauses)}, updated_at = now()
                WHERE id = ${idx} AND tenant_id = ${idx + 1}
                RETURNING *""",
            *params,
        )
        return {"success": True, "data": dict(row)}


# ========== DELETE (soft) ==========


@router.delete("/{employee_id}")
async def delete_employee(request: Request, employee_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        row = await conn.fetchrow(
            """UPDATE employees SET is_active = false, updated_at = now()
               WHERE id = $1 AND tenant_id = $2 RETURNING id, name""",
            employee_id,
            ctx["tenant_id"],
        )
        if not row:
            raise HTTPException(404, detail="Employee not found")
        return {"success": True, "data": dict(row)}


# ========== SALARY CONFIG ==========


@router.get("/{employee_id}/salary-config")
async def get_salary_config(request: Request, employee_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        rows = await conn.fetch(
            """SELECT esc.id, esc.component_id, sc.code as component_code,
                      sc.name as component_name, sc.type, sc.category,
                      esc.amount, esc.percentage, esc.effective_date, esc.end_date
               FROM employee_salary_config esc
               JOIN salary_components sc ON sc.id = esc.component_id
               WHERE esc.tenant_id = $1 AND esc.employee_id = $2
                 AND (esc.end_date IS NULL OR esc.end_date >= CURRENT_DATE)
               ORDER BY sc.sort_order""",
            ctx["tenant_id"],
            employee_id,
        )
        return {"success": True, "data": [dict(r) for r in rows]}


@router.put("/{employee_id}/salary-config")
async def set_salary_config(
    request: Request, employee_id: UUID, body: SetSalaryConfigRequest
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        # Verify employee exists
        emp = await conn.fetchrow(
            "SELECT id FROM employees WHERE id = $1 AND tenant_id = $2",
            employee_id,
            ctx["tenant_id"],
        )
        if not emp:
            raise HTTPException(404, detail="Employee not found")

        async with conn.transaction():
            for cfg in body.configs:
                await conn.execute(
                    """INSERT INTO employee_salary_config
                       (tenant_id, employee_id, component_id, amount, percentage, effective_date)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (tenant_id, employee_id, component_id, effective_date)
                       DO UPDATE SET amount = $4, percentage = $5, updated_at = now()""",
                    ctx["tenant_id"],
                    employee_id,
                    cfg.component_id,
                    cfg.amount,
                    cfg.percentage,
                    cfg.effective_date,
                )

        return {"success": True, "message": f"{len(body.configs)} salary configs saved"}
