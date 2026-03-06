"""
Tax Groups Router — V124 schema.
CRUD for tax_groups + tax_group_items (bundled tax codes).
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg

from ..schemas.tax_groups import (
    CreateTaxGroupRequest,
    UpdateTaxGroupRequest,
    TaxGroupResponse,
    TaxGroupListResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10, command_timeout=30)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


async def _fetch_group_items(conn, group_id: UUID) -> list:
    """Fetch tax_group_items with joined tax_code data."""
    rows = await conn.fetch("""
        SELECT tgi.id, tgi.tax_group_id, tgi.tax_code_id, tc.name as tax_name,
               tc.rate, tgi.sequence
        FROM tax_group_items tgi
        JOIN tax_codes tc ON tc.id = tgi.tax_code_id
        WHERE tgi.tax_group_id = $1
        ORDER BY tgi.sequence ASC
    """, group_id)
    return [
        {"id": str(r["id"]), "tax_group_id": str(r["tax_group_id"]),
         "tax_code_id": str(r["tax_code_id"]), "tax_name": r["tax_name"],
         "rate": float(r["rate"]), "sequence": r["sequence"]}
        for r in rows
    ]


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "tax-groups"}


@router.get("", response_model=TaxGroupListResponse)
async def list_tax_groups(request: Request):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, code, name, is_active, created_at, updated_at
                FROM tax_groups WHERE tenant_id = $1 ORDER BY name ASC
            """, ctx["tenant_id"])
            items = []
            for r in rows:
                group_items = await _fetch_group_items(conn, r["id"])
                items.append({
                    "id": str(r["id"]), "code": r["code"], "name": r["name"],
                    "is_active": r["is_active"], "items": group_items,
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                })
            return {"items": items, "total": len(items)}
    except HTTPException:
        raise
    except asyncpg.exceptions.UndefinedTableError:
        return {"items": [], "total": 0}
    except Exception as e:
        logger.error(f"Error listing tax groups: {e}", exc_info=True)
        return {"items": [], "total": 0}


@router.post("", response_model=TaxGroupResponse, status_code=201)
async def create_tax_group(request: Request, body: CreateTaxGroupRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT id FROM tax_groups WHERE tenant_id = $1 AND code = $2",
                    ctx["tenant_id"], body.code)
                if existing:
                    raise HTTPException(status_code=400, detail=f"Group code '{body.code}' already exists")

                group_id = await conn.fetchval("""
                    INSERT INTO tax_groups (tenant_id, code, name)
                    VALUES ($1, $2, $3) RETURNING id
                """, ctx["tenant_id"], body.code, body.name)

                for item in body.items:
                    await conn.execute("""
                        INSERT INTO tax_group_items (tenant_id, tax_group_id, tax_code_id, sequence)
                        VALUES ($1, $2, $3, $4)
                    """, ctx["tenant_id"], group_id, UUID(item.tax_code_id), item.sequence)

                return {"success": True, "message": "Tax group created",
                        "data": {"id": str(group_id), "code": body.code}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating tax group: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create tax group")


@router.patch("/{group_id}", response_model=TaxGroupResponse)
async def update_tax_group(request: Request, group_id: UUID, body: UpdateTaxGroupRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT id, code FROM tax_groups WHERE id = $1 AND tenant_id = $2",
                    group_id, ctx["tenant_id"])
                if not existing:
                    raise HTTPException(status_code=404, detail="Tax group not found")

                update_data = body.model_dump(exclude_unset=True, exclude={"items"})
                if update_data:
                    if "code" in update_data and update_data["code"] != existing["code"]:
                        dup = await conn.fetchval(
                            "SELECT id FROM tax_groups WHERE tenant_id = $1 AND code = $2 AND id != $3",
                            ctx["tenant_id"], update_data["code"], group_id)
                        if dup:
                            raise HTTPException(status_code=400, detail=f"Group code '{update_data['code']}' already exists")

                    sets = []
                    params = []
                    idx = 1
                    for field, value in update_data.items():
                        sets.append(f"{field} = ${idx}")
                        params.append(value)
                        idx += 1
                    sets.append("updated_at = NOW()")
                    params.append(group_id)
                    params.append(ctx["tenant_id"])
                    await conn.execute(f"UPDATE tax_groups SET {', '.join(sets)} WHERE id = ${idx} AND tenant_id = ${idx + 1}", *params)

                # Replace items if provided
                if body.items is not None:
                    await conn.execute("DELETE FROM tax_group_items WHERE tax_group_id = $1", group_id)
                    for item in body.items:
                        await conn.execute("""
                            INSERT INTO tax_group_items (tenant_id, tax_group_id, tax_code_id, sequence)
                            VALUES ($1, $2, $3, $4)
                        """, ctx["tenant_id"], group_id, UUID(item.tax_code_id), item.sequence)

                return {"success": True, "message": "Tax group updated", "data": {"id": str(group_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating tax group {group_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update tax group")


@router.delete("/{group_id}", response_model=TaxGroupResponse)
async def delete_tax_group(request: Request, group_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT id, code FROM tax_groups WHERE id = $1 AND tenant_id = $2",
                    group_id, ctx["tenant_id"])
                if not existing:
                    raise HTTPException(status_code=404, detail="Tax group not found")
                await conn.execute("DELETE FROM tax_group_items WHERE tax_group_id = $1 AND tenant_id = $2",
                    group_id, ctx["tenant_id"])
                await conn.execute("DELETE FROM tax_groups WHERE id = $1 AND tenant_id = $2",
                    group_id, ctx["tenant_id"])
                return {"success": True, "message": "Tax group deleted", "data": {"id": str(group_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting tax group {group_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete tax group")
