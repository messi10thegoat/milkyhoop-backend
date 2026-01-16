"""
Item Batches Router
===================
Batch/Lot tracking with expiry dates.
Default selection method: FEFO (First Expiry First Out).
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.item_batches import (
    AdjustBatchQuantityRequest,
    AdjustBatchResponse,
    AvailableBatch,
    AvailableBatchesResponse,
    BatchWarehouseStock,
    CreateItemBatchRequest,
    CreateItemBatchResponse,
    ExpiringBatchesResponse,
    ExpiredBatchesResponse,
    ItemBatchData,
    ItemBatchDetailData,
    ItemBatchDetailResponse,
    ItemBatchListResponse,
    ItemBatchesSummaryResponse,
    UpdateItemBatchRequest,
    UpdateItemBatchResponse,
    WarehouseBatchesResponse,
    WarehouseBatchesSummary,
)

router = APIRouter()

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

@router.get("", response_model=ItemBatchListResponse)
async def list_item_batches(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    item_id: Optional[UUID] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """List all batches"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["ib.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if item_id:
            where_clauses.append(f"ib.item_id = ${param_idx}")
            params.append(item_id)
            param_idx += 1

        if status:
            where_clauses.append(f"ib.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if search:
            where_clauses.append(f"ib.batch_number ILIKE ${param_idx}")
            params.append(f"%{search}%")
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM item_batches ib WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT ib.*, i.sku as item_code, i.nama_produk as item_name
            FROM item_batches ib
            LEFT JOIN products i ON ib.item_id = i.id
            WHERE {where_sql}
            ORDER BY ib.expiry_date ASC NULLS LAST, ib.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = [ItemBatchData(**dict(row)) for row in rows]
        return ItemBatchListResponse(data=data, total=total, has_more=(skip + limit) < total)


@router.get("/expiring", response_model=ExpiringBatchesResponse)
async def get_expiring_batches(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    warehouse_id: Optional[UUID] = None,
):
    """Get batches expiring within specified days"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_expiring_batches($1, $2, $3)",
            ctx["tenant_id"], days, warehouse_id
        )

        total_value = sum(row["quantity"] * (row.get("unit_cost", 0) or 0) for row in rows)

        return ExpiringBatchesResponse(
            days_ahead=days,
            data=[dict(row) for row in rows],
            total=len(rows),
            total_value=int(total_value)
        )


@router.get("/expired", response_model=ExpiredBatchesResponse)
async def get_expired_batches(
    request: Request,
    warehouse_id: Optional[UUID] = None,
):
    """Get all expired batches"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_expired_batches($1, $2)",
            ctx["tenant_id"], warehouse_id
        )

        total_value = sum(row.get("total_value", 0) or 0 for row in rows)

        return ExpiredBatchesResponse(
            data=[dict(row) for row in rows],
            total=len(rows),
            total_value=int(total_value)
        )


@router.get("/{batch_id}", response_model=ItemBatchDetailResponse)
async def get_item_batch(request: Request, batch_id: UUID):
    """Get batch details with warehouse breakdown"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            SELECT ib.*, i.sku as item_code, i.nama_produk as item_name
            FROM item_batches ib
            LEFT JOIN products i ON ib.item_id = i.id
            WHERE ib.id = $1 AND ib.tenant_id = $2
            """,
            batch_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Batch not found")

        warehouse_stock = await conn.fetch(
            """
            SELECT bws.warehouse_id, w.name as warehouse_name,
                   bws.quantity, bws.reserved_quantity, bws.available_quantity
            FROM batch_warehouse_stock bws
            JOIN warehouses w ON bws.warehouse_id = w.id
            WHERE bws.batch_id = $1
            """,
            batch_id
        )

        data = ItemBatchDetailData(
            **dict(row),
            warehouse_stock=[BatchWarehouseStock(**dict(ws)) for ws in warehouse_stock]
        )

        return ItemBatchDetailResponse(data=data)


@router.post("", response_model=CreateItemBatchResponse)
async def create_item_batch(request: Request, body: CreateItemBatchRequest):
    """Create a new batch"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Check batch number uniqueness
            existing = await conn.fetchval(
                "SELECT id FROM item_batches WHERE tenant_id = $1 AND item_id = $2 AND batch_number = $3",
                ctx["tenant_id"], body.item_id, body.batch_number
            )
            if existing:
                raise HTTPException(status_code=400, detail="Batch number already exists for this item")

            total_value = int(body.initial_quantity * body.unit_cost)

            row = await conn.fetchrow(
                """
                INSERT INTO item_batches (
                    tenant_id, item_id, batch_number, manufacture_date, expiry_date,
                    received_date, initial_quantity, current_quantity, unit_cost, total_value,
                    purchase_order_id, bill_id, supplier_batch_number, quality_grade, quality_notes,
                    created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                RETURNING *
                """,
                ctx["tenant_id"], body.item_id, body.batch_number, body.manufacture_date,
                body.expiry_date, body.received_date or date.today(), body.initial_quantity,
                body.unit_cost, total_value, body.purchase_order_id, body.bill_id,
                body.supplier_batch_number, body.quality_grade, body.quality_notes,
                ctx.get("user_id")
            )

            batch_id = row["id"]

            # Create warehouse stock if warehouse provided
            if body.warehouse_id:
                await conn.execute(
                    """
                    INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity)
                    VALUES ($1, $2, $3, $4)
                    """,
                    ctx["tenant_id"], batch_id, body.warehouse_id, body.initial_quantity
                )

            # Get item info
            item = await conn.fetchrow("SELECT sku as code, nama_produk as name FROM products WHERE id = $1", body.item_id)

            return CreateItemBatchResponse(
                data=ItemBatchData(
                    **dict(row),
                    item_code=item["code"] if item else None,
                    item_name=item["name"] if item else None
                )
            )


@router.patch("/{batch_id}", response_model=UpdateItemBatchResponse)
async def update_item_batch(request: Request, batch_id: UUID, body: UpdateItemBatchRequest):
    """Update batch details"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM item_batches WHERE id = $1 AND tenant_id = $2",
            batch_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Batch not found")

        updates = []
        params = []
        param_idx = 1

        for field in ["batch_number", "manufacture_date", "expiry_date",
                      "supplier_batch_number", "quality_grade", "quality_notes", "status"]:
            value = getattr(body, field, None)
            if value is not None:
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

        if not updates:
            return UpdateItemBatchResponse(data=ItemBatchData(**dict(existing)))

        params.append(batch_id)

        row = await conn.fetchrow(
            f"""
            UPDATE item_batches SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
            RETURNING *
            """,
            *params
        )

        item = await conn.fetchrow("SELECT sku as code, nama_produk as name FROM products WHERE id = $1", row["item_id"])

        return UpdateItemBatchResponse(
            data=ItemBatchData(
                **dict(row),
                item_code=item["code"] if item else None,
                item_name=item["name"] if item else None
            )
        )


@router.post("/{batch_id}/adjust", response_model=AdjustBatchResponse)
async def adjust_batch_quantity(request: Request, batch_id: UUID, body: AdjustBatchQuantityRequest):
    """Adjust batch quantity in a warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            batch = await conn.fetchrow(
                "SELECT * FROM item_batches WHERE id = $1 AND tenant_id = $2",
                batch_id, ctx["tenant_id"]
            )

            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")

            # Update warehouse stock
            await conn.execute(
                """
                INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (batch_id, warehouse_id)
                DO UPDATE SET quantity = batch_warehouse_stock.quantity + EXCLUDED.quantity,
                              updated_at = NOW()
                """,
                ctx["tenant_id"], batch_id, body.warehouse_id, body.quantity_change
            )

            # Get updated batch
            row = await conn.fetchrow(
                "SELECT * FROM item_batches WHERE id = $1",
                batch_id
            )

            ws = await conn.fetchrow(
                """
                SELECT bws.*, w.name as warehouse_name
                FROM batch_warehouse_stock bws
                JOIN warehouses w ON bws.warehouse_id = w.id
                WHERE bws.batch_id = $1 AND bws.warehouse_id = $2
                """,
                batch_id, body.warehouse_id
            )

            item = await conn.fetchrow("SELECT sku as code, nama_produk as name FROM products WHERE id = $1", row["item_id"])

            return AdjustBatchResponse(
                data=ItemBatchData(
                    **dict(row),
                    item_code=item["code"] if item else None,
                    item_name=item["name"] if item else None
                ),
                warehouse_stock=BatchWarehouseStock(**dict(ws))
            )


@router.get("/items/{item_id}/batches/available", response_model=AvailableBatchesResponse)
async def get_available_batches_for_item(
    request: Request,
    item_id: UUID,
    warehouse_id: UUID = Query(...),
    quantity: Decimal = Query(..., gt=0),
    method: str = Query("FEFO", pattern="^(FEFO|FIFO)$"),
):
    """Get available batches for selection using FEFO or FIFO"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_available_batches($1, $2, $3, $4, $5)",
            ctx["tenant_id"], item_id, warehouse_id, quantity, method
        )

        total_available = sum(row["available_quantity"] for row in rows)
        total_allocated = sum(row["quantity_to_use"] for row in rows)

        batches = []
        for row in rows:
            days_until = None
            if row["expiry_date"]:
                days_until = (row["expiry_date"] - date.today()).days

            batches.append(AvailableBatch(
                batch_id=row["batch_id"],
                batch_number=row["batch_number"],
                expiry_date=row["expiry_date"],
                days_until_expiry=days_until,
                available_quantity=row["available_quantity"],
                quantity_to_use=row["quantity_to_use"],
                unit_cost=row["unit_cost"]
            ))

        return AvailableBatchesResponse(
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity_requested=quantity,
            quantity_available=total_available,
            quantity_allocated=total_allocated,
            fully_satisfied=total_allocated >= quantity,
            method=method,
            batches=batches
        )
