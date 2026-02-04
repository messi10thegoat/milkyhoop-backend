"""
Warehouse Bins Router - Sub-location Management
"""
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings

router = APIRouter()

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


@router.get("")
async def list_bins(
    request: Request,
    warehouse_id: Optional[str] = Query(None),
    bin_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """List warehouse bins"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        query = """
            SELECT wb.id, wb.warehouse_id, w.name as warehouse_name,
                wb.code, wb.name, wb.biN_type, wb.is_active, wb.is_default
            FROM warehouse_bins wb
            JOIN warehouses w ON wb.warehouse_id = w.id
            WHERE wb.tenant_id = $1
        """
        params = [ctx["tenant_id"]]
        idx = 1

        if warehouse_id:
            idx += 1
            query += f" AND wb.warehouse_id = ${idx}::uuid"
            params.append(warehouse_id)

        if bin_type:
            idx += 1
            query += f" AND wb.bin_type = ${idx}"
            params.append(bin_type)

        if is_active is not None:
            idx += 1
            query += f" AND wb.is_active = ${idx}"
            params.append(is_active)

        idx += 1
        query += f" ORDER BY w.name, wb.code LIMIT ${idx}"
        params.append(limit)
        idx += 1
        query += f" OFFSET ${idx}"
        params.append(offset)

        rows = await conn.fetch(query, *params)

        bins = []
        for r in rows:
            bins.append({
                "id": str(r["id"]),
                "warehouse_id": str(r["warehouse_id"]),
                "warehouse_name": r["warehouse_name"],
                "code": r["code"],
                "name": r["name"],
                "bin_type": r["bin_type"],
                "is_active": r["is_active"],
                "is_default": r["is_default"]
            })

        return {"success": True, "bins": bins, "total": len(bins)}


@router.get("/{bin_id}")
async def get_bin(request: Request, bin_id: str):
    """Get bin details"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow("""
            SELECT wb.*, w.name as warehouse_name
            FROM warehouse_bins wb
            JOIN warehouses w ON wb.warehouse_id = w.id
            WHERE wb.tenant_id = $1 AND wb.id = $2::uuid
        """, ctx["tenant_id"], bin_id)

        if not row:
            raise HTTPException(status_code=404, detail="Bin not found")

        return {
            "success": True,
            "data": {
                "id": str(row["id"]),
                "warehouse_id": str(row["warehouse_id"]),
                "warehouse_name": row["warehouse_name"],
                "code": row["code"],
                "name": row["name"],
                "bin_type": row["bin_type"],
                "is_active": row["is_active"],
                "is_default": row["is_default"]
            }
        }


@router.post("")
async def create_bin(request: Request):
    """Create warehouse bin"""
    ctx = get_user_context(request)
    body = await request.json()

    for field in ["warehouse_id", "code", "name"]:
        if not body.get(field):
            raise HTTPException(status_code=400, detail=f"{field} is required")

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow("""
            INSERT INTO warehouse_bins (
                tenant_id, warehouse_id, code, name,
                aisle, rack, shelf, position, bin_type,
                is_active, is_default
            ) VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
        """,
            ctx["tenant_id"], body["warehouse_id"], body["code"], body["name"],
            body.get("aisle"), body.get("rack"), body.get("shelf"),
            body.get("position"), body.get("bin_type", "storage"),
            body.get("is_active", True), body.get("is_default", False)
        )

        return {"success": True, "message": "Bin created", "data": {"id": str(row["id"])}}


@router.delete("/{bin_id}")
async def delete_bin(request: Request, bin_id: str):
    """Delete warehouse bin"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        has_stock = await conn.fetchval("""
            SELECT EXISTS(SELECT 1 FROM bin_stock WHERE tenant_id = $1 AND bin_id = $2::uuid AND quantity > 0)
        """, ctx["tenant_id"], bin_id)

        if has_stock:
            raise HTTPException(status_code=400, detail="Cannot delete bin with stock")

        result = await conn.execute(
            "DELETE FROM warehouse_bins WHERE tenant_id = $1 AND id = $2::uuid",
            ctx["tenant_id"], bin_id
        )

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Bin not found")

        return {"success": True, "message": "Bin deleted"}
