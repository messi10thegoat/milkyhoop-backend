"""
Warehouses Router
=================
Multi-warehouse/location management endpoints.
"""
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.warehouses import (
    CreateWarehouseRequest,
    CreateWarehouseResponse,
    DeleteWarehouseResponse,
    ItemStockByWarehouseResponse,
    LowStockResponse,
    UpdateWarehouseRequest,
    UpdateWarehouseResponse,
    WarehouseData,
    WarehouseDetailResponse,
    WarehouseListResponse,
    WarehouseStockResponse,
    WarehouseStockValueResponse,
)

router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
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


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("", response_model=WarehouseListResponse)
async def list_warehouses(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    is_branch: Optional[bool] = None,
):
    """List all warehouses"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if search:
            where_clauses.append(f"(code ILIKE ${param_idx} OR name ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1

        if is_active is not None:
            where_clauses.append(f"is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        if is_branch is not None:
            where_clauses.append(f"is_branch = ${param_idx}")
            params.append(is_branch)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        # Count total
        total = await conn.fetchval(f"SELECT COUNT(*) FROM warehouses WHERE {where_sql}", *params)

        # Fetch warehouses
        rows = await conn.fetch(
            f"""
            SELECT * FROM warehouses
            WHERE {where_sql}
            ORDER BY is_default DESC, name ASC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = [WarehouseData(**dict(row)) for row in rows]

        return WarehouseListResponse(data=data, total=total)


@router.get("/{warehouse_id}", response_model=WarehouseDetailResponse)
async def get_warehouse(request: Request, warehouse_id: UUID):
    """Get warehouse details"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            "SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2",
            warehouse_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        return WarehouseDetailResponse(data=WarehouseData(**dict(row)))


@router.post("", response_model=CreateWarehouseResponse)
async def create_warehouse(request: Request, body: CreateWarehouseRequest):
    """Create a new warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Check code uniqueness
        existing = await conn.fetchval(
            "SELECT id FROM warehouses WHERE tenant_id = $1 AND code = $2",
            ctx["tenant_id"], body.code
        )
        if existing:
            raise HTTPException(status_code=400, detail=f"Warehouse code '{body.code}' already exists")

        row = await conn.fetchrow(
            """
            INSERT INTO warehouses (
                tenant_id, code, name, address, city, province, postal_code, country,
                phone, email, manager_name, is_default, is_active, is_branch, branch_code,
                created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            RETURNING *
            """,
            ctx["tenant_id"], body.code, body.name, body.address, body.city,
            body.province, body.postal_code, body.country, body.phone, body.email,
            body.manager_name, body.is_default, body.is_active, body.is_branch,
            body.branch_code, ctx.get("user_id")
        )

        return CreateWarehouseResponse(data=WarehouseData(**dict(row)))


@router.patch("/{warehouse_id}", response_model=UpdateWarehouseResponse)
async def update_warehouse(request: Request, warehouse_id: UUID, body: UpdateWarehouseRequest):
    """Update warehouse details"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Check exists
        existing = await conn.fetchrow(
            "SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2",
            warehouse_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Check code uniqueness if changing
        if body.code and body.code != existing["code"]:
            code_exists = await conn.fetchval(
                "SELECT id FROM warehouses WHERE tenant_id = $1 AND code = $2 AND id != $3",
                ctx["tenant_id"], body.code, warehouse_id
            )
            if code_exists:
                raise HTTPException(status_code=400, detail=f"Warehouse code '{body.code}' already exists")

        # Build update
        updates = []
        params = []
        param_idx = 1

        for field in ["code", "name", "address", "city", "province", "postal_code",
                      "country", "phone", "email", "manager_name", "is_default",
                      "is_active", "is_branch", "branch_code"]:
            value = getattr(body, field, None)
            if value is not None:
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

        if not updates:
            return UpdateWarehouseResponse(data=WarehouseData(**dict(existing)))

        params.extend([warehouse_id, ctx["tenant_id"]])

        row = await conn.fetchrow(
            f"""
            UPDATE warehouses SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            RETURNING *
            """,
            *params
        )

        return UpdateWarehouseResponse(data=WarehouseData(**dict(row)))


@router.delete("/{warehouse_id}", response_model=DeleteWarehouseResponse)
async def delete_warehouse(request: Request, warehouse_id: UUID):
    """Deactivate warehouse (soft delete)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Check exists and has no stock
        existing = await conn.fetchrow(
            "SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2",
            warehouse_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Check for stock
        stock_count = await conn.fetchval(
            "SELECT COUNT(*) FROM warehouse_stock WHERE warehouse_id = $1 AND quantity > 0",
            warehouse_id
        )
        if stock_count > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete warehouse with existing stock. Transfer stock first."
            )

        await conn.execute(
            "UPDATE warehouses SET is_active = false, updated_at = NOW() WHERE id = $1",
            warehouse_id
        )

        return DeleteWarehouseResponse()


@router.get("/{warehouse_id}/stock", response_model=WarehouseStockResponse)
async def get_warehouse_stock(
    request: Request,
    warehouse_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
):
    """Get stock in warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Verify warehouse
        wh = await conn.fetchrow(
            "SELECT id, name FROM warehouses WHERE id = $1 AND tenant_id = $2",
            warehouse_id, ctx["tenant_id"]
        )
        if not wh:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        where_extra = ""
        params = [warehouse_id, ctx["tenant_id"]]
        if search:
            where_extra = " AND (i.code ILIKE $3 OR i.name ILIKE $3)"
            params.append(f"%{search}%")

        total = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM warehouse_stock ws
            JOIN items i ON ws.item_id = i.id
            WHERE ws.warehouse_id = $1 AND ws.tenant_id = $2 {where_extra}
            """,
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT
                ws.item_id, i.code as item_code, i.name as item_name,
                ws.quantity, ws.reserved_quantity, ws.available_quantity,
                i.unit, i.unit_cost, (ws.quantity * COALESCE(i.unit_cost, 0))::BIGINT as total_value,
                ws.reorder_level, ws.reorder_quantity, ws.last_stock_date
            FROM warehouse_stock ws
            JOIN items i ON ws.item_id = i.id
            WHERE ws.warehouse_id = $1 AND ws.tenant_id = $2 {where_extra}
            ORDER BY i.name ASC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params, limit, skip
        )

        return WarehouseStockResponse(
            data=[dict(row) for row in rows],
            total=total,
            warehouse_id=warehouse_id,
            warehouse_name=wh["name"]
        )


@router.get("/{warehouse_id}/stock-value", response_model=WarehouseStockValueResponse)
async def get_warehouse_stock_value(request: Request, warehouse_id: UUID):
    """Get total stock value in warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        wh = await conn.fetchrow(
            "SELECT id, name FROM warehouses WHERE id = $1 AND tenant_id = $2",
            warehouse_id, ctx["tenant_id"]
        )
        if not wh:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        row = await conn.fetchrow(
            """
            SELECT
                COUNT(DISTINCT ws.item_id)::INT as total_items,
                COALESCE(SUM(ws.quantity), 0) as total_quantity,
                COALESCE(SUM(ws.quantity * COALESCE(i.unit_cost, 0)), 0)::BIGINT as total_value
            FROM warehouse_stock ws
            JOIN items i ON ws.item_id = i.id
            WHERE ws.warehouse_id = $1 AND ws.tenant_id = $2 AND ws.quantity > 0
            """,
            warehouse_id, ctx["tenant_id"]
        )

        return WarehouseStockValueResponse(
            warehouse_id=warehouse_id,
            warehouse_name=wh["name"],
            total_items=row["total_items"],
            total_quantity=row["total_quantity"],
            total_value=row["total_value"]
        )


@router.get("/low-stock", response_model=LowStockResponse)
async def get_low_stock_items(
    request: Request,
    warehouse_id: Optional[UUID] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """Get items below reorder level"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_extra = ""
        params = [ctx["tenant_id"]]
        if warehouse_id:
            where_extra = " AND ws.warehouse_id = $2"
            params.append(warehouse_id)

        rows = await conn.fetch(
            f"""
            SELECT
                ws.item_id, i.code as item_code, i.name as item_name,
                ws.warehouse_id, w.name as warehouse_name,
                ws.quantity, ws.reorder_level,
                (ws.reorder_level - ws.quantity) as shortage
            FROM warehouse_stock ws
            JOIN items i ON ws.item_id = i.id
            JOIN warehouses w ON ws.warehouse_id = w.id
            WHERE ws.tenant_id = $1
            AND ws.reorder_level IS NOT NULL
            AND ws.quantity <= ws.reorder_level
            {where_extra}
            ORDER BY shortage DESC
            LIMIT ${len(params) + 1}
            """,
            *params, limit
        )

        return LowStockResponse(data=[dict(row) for row in rows], total=len(rows))


@router.post("/{warehouse_id}/set-default")
async def set_default_warehouse(request: Request, warehouse_id: UUID):
    """Set warehouse as default"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM warehouses WHERE id = $1 AND tenant_id = $2",
            warehouse_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        await conn.execute(
            "UPDATE warehouses SET is_default = true WHERE id = $1",
            warehouse_id
        )

        return {"success": True, "message": "Warehouse set as default"}
