"""
Item Serials Router
===================
Serial number tracking for individual units.
"""
from datetime import date
from typing import List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.item_serials import (
    AdjustSerialRequest,
    AdjustSerialResponse,
    AvailableSerial,
    AvailableSerialsResponse,
    BulkCreateSerialsRequest,
    BulkCreateSerialsResponse,
    CreateItemSerialRequest,
    CreateItemSerialResponse,
    ItemSerialData,
    ItemSerialDetailData,
    ItemSerialDetailResponse,
    ItemSerialListResponse,
    SearchSerialResponse,
    SearchSerialResult,
    SerialHistoryResponse,
    SerialMovementData,
    TransferSerialRequest,
    TransferSerialResponse,
    UpdateItemSerialRequest,
    UpdateItemSerialResponse,
    WarehouseSerialsResponse,
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

@router.get("", response_model=ItemSerialListResponse)
async def list_item_serials(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    item_id: Optional[UUID] = None,
    warehouse_id: Optional[UUID] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """List all serial numbers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["s.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if item_id:
            where_clauses.append(f"s.item_id = ${param_idx}")
            params.append(item_id)
            param_idx += 1

        if warehouse_id:
            where_clauses.append(f"s.warehouse_id = ${param_idx}")
            params.append(warehouse_id)
            param_idx += 1

        if status:
            where_clauses.append(f"s.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if search:
            where_clauses.append(f"s.serial_number ILIKE ${param_idx}")
            params.append(f"%{search}%")
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM item_serials s WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT s.*, i.sku as item_code, i.nama_produk as item_name,
                   w.name as warehouse_name, c.nama as customer_name,
                   b.batch_number
            FROM item_serials s
            LEFT JOIN products i ON s.item_id = i.id
            LEFT JOIN warehouses w ON s.warehouse_id = w.id
            LEFT JOIN customers c ON s.customer_id::text = c.id
            LEFT JOIN item_batches b ON s.batch_id = b.id
            WHERE {where_sql}
            ORDER BY s.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = [ItemSerialData(**dict(row)) for row in rows]
        return ItemSerialListResponse(data=data, total=total, has_more=(skip + limit) < total)


@router.get("/search", response_model=SearchSerialResponse)
async def search_serial_number(
    request: Request,
    serial_number: str = Query(..., min_length=1),
):
    """Search for serial numbers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM search_serial_number($1, $2)",
            ctx["tenant_id"], serial_number
        )

        return SearchSerialResponse(
            query=serial_number,
            data=[SearchSerialResult(**dict(row)) for row in rows],
            total=len(rows)
        )


@router.get("/{serial_id}", response_model=ItemSerialDetailResponse)
async def get_item_serial(request: Request, serial_id: UUID):
    """Get serial details with movement history"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            SELECT s.*, i.sku as item_code, i.nama_produk as item_name,
                   w.name as warehouse_name, c.nama as customer_name,
                   b.batch_number
            FROM item_serials s
            LEFT JOIN products i ON s.item_id = i.id
            LEFT JOIN warehouses w ON s.warehouse_id = w.id
            LEFT JOIN customers c ON s.customer_id::text = c.id
            LEFT JOIN item_batches b ON s.batch_id = b.id
            WHERE s.id = $1 AND s.tenant_id = $2
            """,
            serial_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Serial not found")

        movements = await conn.fetch(
            """
            SELECT sm.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM serial_movements sm
            LEFT JOIN warehouses fw ON sm.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON sm.to_warehouse_id = tw.id
            WHERE sm.serial_id = $1
            ORDER BY sm.movement_date DESC
            """,
            serial_id
        )

        data = ItemSerialDetailData(
            **dict(row),
            movements=[SerialMovementData(**dict(m)) for m in movements]
        )

        return ItemSerialDetailResponse(data=data)


@router.post("", response_model=CreateItemSerialResponse)
async def create_item_serial(request: Request, body: CreateItemSerialRequest):
    """Create a single serial number"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Check uniqueness
            existing = await conn.fetchval(
                "SELECT id FROM item_serials WHERE tenant_id = $1 AND item_id = $2 AND serial_number = $3",
                ctx["tenant_id"], body.item_id, body.serial_number
            )
            if existing:
                raise HTTPException(status_code=400, detail="Serial number already exists for this item")

            row = await conn.fetchrow(
                """
                INSERT INTO item_serials (
                    tenant_id, item_id, serial_number, warehouse_id, received_date,
                    warranty_start_date, warranty_expiry, unit_cost, selling_price,
                    purchase_order_id, bill_id, supplier_serial, batch_id,
                    condition, condition_notes, notes, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                RETURNING *
                """,
                ctx["tenant_id"], body.item_id, body.serial_number, body.warehouse_id,
                body.received_date or date.today(), body.warranty_start_date, body.warranty_expiry,
                body.unit_cost, body.selling_price, body.purchase_order_id, body.bill_id,
                body.supplier_serial, body.batch_id, body.condition, body.condition_notes,
                body.notes, ctx.get("user_id")
            )

            serial_id = row["id"]

            # Create initial movement
            await conn.execute(
                """
                INSERT INTO serial_movements (
                    tenant_id, serial_id, movement_type, to_warehouse_id, to_status, performed_by
                ) VALUES ($1, $2, 'received', $3, 'available', $4)
                """,
                ctx["tenant_id"], serial_id, body.warehouse_id, ctx.get("user_id")
            )

            item = await conn.fetchrow("SELECT sku as code, nama_produk as name FROM products WHERE id = $1", body.item_id)
            wh = await conn.fetchrow("SELECT name FROM warehouses WHERE id = $1", body.warehouse_id) if body.warehouse_id else None

            return CreateItemSerialResponse(
                data=ItemSerialData(
                    **dict(row),
                    item_code=item["code"] if item else None,
                    item_name=item["name"] if item else None,
                    warehouse_name=wh["name"] if wh else None
                )
            )


@router.post("/bulk", response_model=BulkCreateSerialsResponse)
async def bulk_create_serials(request: Request, body: BulkCreateSerialsRequest):
    """Create multiple serial numbers at once"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            created = []
            for serial_number in body.serial_numbers:
                # Check uniqueness
                existing = await conn.fetchval(
                    "SELECT id FROM item_serials WHERE tenant_id = $1 AND item_id = $2 AND serial_number = $3",
                    ctx["tenant_id"], body.item_id, serial_number
                )
                if existing:
                    continue  # Skip duplicates

                row = await conn.fetchrow(
                    """
                    INSERT INTO item_serials (
                        tenant_id, item_id, serial_number, warehouse_id, received_date,
                        unit_cost, purchase_order_id, bill_id, batch_id, condition, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING *
                    """,
                    ctx["tenant_id"], body.item_id, serial_number, body.warehouse_id,
                    body.received_date or date.today(), body.unit_cost, body.purchase_order_id,
                    body.bill_id, body.batch_id, body.condition, ctx.get("user_id")
                )

                # Create movement
                await conn.execute(
                    """
                    INSERT INTO serial_movements (
                        tenant_id, serial_id, movement_type, to_warehouse_id, to_status, performed_by
                    ) VALUES ($1, $2, 'received', $3, 'available', $4)
                    """,
                    ctx["tenant_id"], row["id"], body.warehouse_id, ctx.get("user_id")
                )

                created.append(ItemSerialData(**dict(row)))

            return BulkCreateSerialsResponse(
                data=created,
                created_count=len(created)
            )


@router.post("/{serial_id}/transfer", response_model=TransferSerialResponse)
async def transfer_serial(request: Request, serial_id: UUID, body: TransferSerialRequest):
    """Transfer serial to another warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            serial = await conn.fetchrow(
                "SELECT * FROM item_serials WHERE id = $1 AND tenant_id = $2",
                serial_id, ctx["tenant_id"]
            )

            if not serial:
                raise HTTPException(status_code=404, detail="Serial not found")

            if serial["status"] != "available":
                raise HTTPException(status_code=400, detail="Only available serials can be transferred")

            # Record movement
            movement_id = await conn.fetchval(
                "SELECT record_serial_movement($1, $2, 'transferred', $3, NULL, NULL, NULL, NULL, $4, $5)",
                ctx["tenant_id"], serial_id, body.to_warehouse_id, ctx.get("user_id"), body.notes
            )

            # Get updated serial
            row = await conn.fetchrow(
                """
                SELECT s.*, i.sku as item_code, i.nama_produk as item_name, w.name as warehouse_name
                FROM item_serials s
                LEFT JOIN products i ON s.item_id = i.id
                LEFT JOIN warehouses w ON s.warehouse_id = w.id
                WHERE s.id = $1
                """,
                serial_id
            )

            movement = await conn.fetchrow(
                """
                SELECT sm.*, fw.name as from_warehouse_name, tw.name as to_warehouse_name
                FROM serial_movements sm
                LEFT JOIN warehouses fw ON sm.from_warehouse_id = fw.id
                LEFT JOIN warehouses tw ON sm.to_warehouse_id = tw.id
                WHERE sm.id = $1
                """,
                movement_id
            )

            return TransferSerialResponse(
                data=ItemSerialData(**dict(row)),
                movement=SerialMovementData(**dict(movement))
            )


@router.post("/{serial_id}/adjust", response_model=AdjustSerialResponse)
async def adjust_serial_status(request: Request, serial_id: UUID, body: AdjustSerialRequest):
    """Adjust serial status (mark as damaged, scrapped, etc.)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            serial = await conn.fetchrow(
                "SELECT * FROM item_serials WHERE id = $1 AND tenant_id = $2",
                serial_id, ctx["tenant_id"]
            )

            if not serial:
                raise HTTPException(status_code=404, detail="Serial not found")

            movement_type = "adjusted"
            if body.status == "damaged":
                movement_type = "damaged"
            elif body.status == "scrapped":
                movement_type = "scrapped"

            movement_id = await conn.fetchval(
                "SELECT record_serial_movement($1, $2, $3, NULL, $4, 'adjustment', NULL, NULL, $5, $6)",
                ctx["tenant_id"], serial_id, movement_type, body.status, ctx.get("user_id"), body.reason
            )

            row = await conn.fetchrow(
                """
                SELECT s.*, i.sku as item_code, i.nama_produk as item_name, w.name as warehouse_name
                FROM item_serials s
                LEFT JOIN products i ON s.item_id = i.id
                LEFT JOIN warehouses w ON s.warehouse_id = w.id
                WHERE s.id = $1
                """,
                serial_id
            )

            movement = await conn.fetchrow(
                """
                SELECT sm.*, fw.name as from_warehouse_name, tw.name as to_warehouse_name
                FROM serial_movements sm
                LEFT JOIN warehouses fw ON sm.from_warehouse_id = fw.id
                LEFT JOIN warehouses tw ON sm.to_warehouse_id = tw.id
                WHERE sm.id = $1
                """,
                movement_id
            )

            return AdjustSerialResponse(
                data=ItemSerialData(**dict(row)),
                movement=SerialMovementData(**dict(movement))
            )


@router.get("/{serial_id}/history", response_model=SerialHistoryResponse)
async def get_serial_history(request: Request, serial_id: UUID):
    """Get movement history for a serial"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        serial = await conn.fetchrow(
            "SELECT id, serial_number FROM item_serials WHERE id = $1 AND tenant_id = $2",
            serial_id, ctx["tenant_id"]
        )

        if not serial:
            raise HTTPException(status_code=404, detail="Serial not found")

        rows = await conn.fetch(
            "SELECT * FROM get_serial_history($1, $2)",
            ctx["tenant_id"], serial_id
        )

        return SerialHistoryResponse(
            serial_id=serial_id,
            serial_number=serial["serial_number"],
            data=[SerialMovementData(**dict(row)) for row in rows],
            total=len(rows)
        )


@router.get("/items/{item_id}/serials/available", response_model=AvailableSerialsResponse)
async def get_available_serials_for_item(
    request: Request,
    item_id: UUID,
    warehouse_id: UUID = Query(...),
    limit: int = Query(50, ge=1, le=200),
):
    """Get available serials for an item in a warehouse"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_available_serials($1, $2, $3, $4)",
            ctx["tenant_id"], item_id, warehouse_id, limit
        )

        return AvailableSerialsResponse(
            item_id=item_id,
            warehouse_id=warehouse_id,
            data=[AvailableSerial(**dict(row)) for row in rows],
            total=len(rows)
        )


@router.get("/warehouses/{warehouse_id}/serials", response_model=WarehouseSerialsResponse)
async def get_warehouse_serials(
    request: Request,
    warehouse_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: str = Query("available"),
):
    """Get serials in a warehouse"""
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

        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM item_serials
            WHERE tenant_id = $1 AND warehouse_id = $2 AND status = $3
            """,
            ctx["tenant_id"], warehouse_id, status
        )

        rows = await conn.fetch(
            """
            SELECT s.*, i.sku as item_code, i.nama_produk as item_name
            FROM item_serials s
            LEFT JOIN products i ON s.item_id = i.id
            WHERE s.tenant_id = $1 AND s.warehouse_id = $2 AND s.status = $3
            ORDER BY s.serial_number ASC
            LIMIT $4 OFFSET $5
            """,
            ctx["tenant_id"], warehouse_id, status, limit, skip
        )

        available_count = await conn.fetchval(
            "SELECT COUNT(*) FROM item_serials WHERE tenant_id = $1 AND warehouse_id = $2 AND status = 'available'",
            ctx["tenant_id"], warehouse_id
        )

        return WarehouseSerialsResponse(
            warehouse_id=warehouse_id,
            warehouse_name=wh["name"],
            data=[ItemSerialData(**dict(row)) for row in rows],
            total=total,
            available_count=available_count
        )
