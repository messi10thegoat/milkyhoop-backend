"""
Salary Components Router — Template komponen gaji per tenant
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg

from ..schemas.payroll import CreateSalaryComponentRequest, UpdateSalaryComponentRequest
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


@router.get("")
async def list_components(
    request: Request,
    type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        conditions = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        idx = 2
        if type:
            conditions.append(f"type = ${idx}")
            params.append(type)
            idx += 1
        if is_active is not None:
            conditions.append(f"is_active = ${idx}")
            params.append(is_active)
            idx += 1
        where = " AND ".join(conditions)
        rows = await conn.fetch(
            f"SELECT * FROM salary_components WHERE {where} ORDER BY sort_order, code",
            *params,
        )
        return {"success": True, "data": [dict(r) for r in rows]}


@router.post("")
async def create_component(request: Request, body: CreateSalaryComponentRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        try:
            row = await conn.fetchrow(
                """INSERT INTO salary_components
                   (tenant_id, code, name, type, category, is_taxable, is_fixed,
                    default_amount, calculation_method, percentage_base, sort_order, is_system)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, false)
                   RETURNING *""",
                ctx["tenant_id"],
                body.code,
                body.name,
                body.type,
                body.category,
                body.is_taxable,
                body.is_fixed,
                body.default_amount,
                body.calculation_method,
                body.percentage_base,
                body.sort_order,
            )
            return {"success": True, "data": dict(row)}
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                409, detail=f"Component code '{body.code}' already exists"
            )


@router.put("/{component_id}")
async def update_component(
    request: Request, component_id: UUID, body: UpdateSalaryComponentRequest
):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        existing = await conn.fetchrow(
            "SELECT id, is_system FROM salary_components WHERE id = $1 AND tenant_id = $2",
            component_id,
            ctx["tenant_id"],
        )
        if not existing:
            raise HTTPException(404, detail="Component not found")

        updates = body.dict(exclude_unset=True)
        if not updates:
            raise HTTPException(400, detail="No fields to update")

        set_clauses = []
        params = []
        idx = 1
        for key, val in updates.items():
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
            idx += 1
        params.append(component_id)
        params.append(ctx["tenant_id"])

        try:
            row = await conn.fetchrow(
                f"""UPDATE salary_components SET {', '.join(set_clauses)}, updated_at = now()
                    WHERE id = ${idx} AND tenant_id = ${idx + 1} RETURNING *""",
                *params,
            )
            return {"success": True, "data": dict(row)}
        except Exception as e:
            if "system salary component" in str(e):
                raise HTTPException(403, detail=str(e))
            raise


@router.delete("/{component_id}")
async def delete_component(request: Request, component_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        existing = await conn.fetchrow(
            "SELECT id, is_system FROM salary_components WHERE id = $1 AND tenant_id = $2",
            component_id,
            ctx["tenant_id"],
        )
        if not existing:
            raise HTTPException(404, detail="Component not found")
        if existing["is_system"]:
            raise HTTPException(403, detail="Cannot delete system salary component")

        await conn.execute(
            "UPDATE salary_components SET is_active = false, updated_at = now() WHERE id = $1",
            component_id,
        )
        return {"success": True, "message": "Component deactivated"}
