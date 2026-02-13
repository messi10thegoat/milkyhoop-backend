"""
Stock Transfers Router
======================
Inter-warehouse stock transfer endpoints.
IMPORTANT: NO JOURNAL ENTRIES - internal stock movement only.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.stock_transfers import (
    CancelTransferRequest,
    CancelTransferResponse,
    CreateStockTransferRequest,
    CreateStockTransferResponse,
    InTransitResponse,
    InTransitSummary,
    ReceiveTransferRequest,
    ReceiveTransferResponse,
    ShipTransferRequest,
    ShipTransferResponse,
    StockTransferData,
    StockTransferDetailData,
    StockTransferDetailResponse,
    StockTransferItemData,
    StockTransferListResponse,
    UpdateStockTransferRequest,
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

@router.get("", response_model=StockTransferListResponse)
async def list_stock_transfers(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = None,
    from_warehouse_id: Optional[UUID] = None,
    to_warehouse_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    """List stock transfers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["st.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"st.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if from_warehouse_id:
            where_clauses.append(f"st.from_warehouse_id = ${param_idx}")
            params.append(from_warehouse_id)
            param_idx += 1

        if to_warehouse_id:
            where_clauses.append(f"st.to_warehouse_id = ${param_idx}")
            params.append(to_warehouse_id)
            param_idx += 1

        if date_from:
            where_clauses.append(f"st.transfer_date >= ${param_idx}")
            params.append(date_from)
            param_idx += 1

        if date_to:
            where_clauses.append(f"st.transfer_date <= ${param_idx}")
            params.append(date_to)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM stock_transfers st WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT st.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM stock_transfers st
            LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
            WHERE {where_sql}
            ORDER BY st.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = [StockTransferData(**dict(row)) for row in rows]

        return StockTransferListResponse(data=data, total=total, has_more=(skip + limit) < total)


@router.get("/in-transit", response_model=InTransitResponse)
async def get_in_transit_transfers(request: Request):
    """Get all in-transit transfers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            """
            SELECT st.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM stock_transfers st
            LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
            WHERE st.tenant_id = $1 AND st.status = 'in_transit'
            ORDER BY st.shipped_date ASC
            """,
            ctx["tenant_id"]
        )

        data = [StockTransferData(**dict(row)) for row in rows]

        summary_row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::INT as total_transfers,
                COALESCE(SUM(total_items), 0)::INT as total_items,
                COALESCE(SUM(total_value), 0)::BIGINT as total_value,
                MIN(shipped_date) as oldest_transfer_date
            FROM stock_transfers
            WHERE tenant_id = $1 AND status = 'in_transit'
            """,
            ctx["tenant_id"]
        )

        return InTransitResponse(
            summary=InTransitSummary(**dict(summary_row)),
            data=data
        )


@router.get("/{transfer_id}", response_model=StockTransferDetailResponse)
async def get_stock_transfer(request: Request, transfer_id: UUID):
    """Get stock transfer details with items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            SELECT st.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM stock_transfers st
            LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
            WHERE st.id = $1 AND st.tenant_id = $2
            """,
            transfer_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Stock transfer not found")

        items = await conn.fetch(
            """
            SELECT * FROM stock_transfer_items
            WHERE stock_transfer_id = $1
            ORDER BY line_number ASC
            """,
            transfer_id
        )

        data = StockTransferDetailData(
            **dict(row),
            items=[StockTransferItemData(**dict(item)) for item in items]
        )

        return StockTransferDetailResponse(data=data)


@router.post("", response_model=CreateStockTransferResponse)
async def create_stock_transfer(request: Request, body: CreateStockTransferRequest):
    """Create a new stock transfer (draft)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Verify warehouses
            from_wh = await conn.fetchrow(
                "SELECT id, name FROM warehouses WHERE id = $1 AND tenant_id = $2",
                body.from_warehouse_id, ctx["tenant_id"]
            )
            if not from_wh:
                raise HTTPException(status_code=400, detail="Source warehouse not found")

            to_wh = await conn.fetchrow(
                "SELECT id, name FROM warehouses WHERE id = $1 AND tenant_id = $2",
                body.to_warehouse_id, ctx["tenant_id"]
            )
            if not to_wh:
                raise HTTPException(status_code=400, detail="Destination warehouse not found")

            # Generate transfer number
            transfer_number = await conn.fetchval(
                "SELECT generate_stock_transfer_number($1)",
                ctx["tenant_id"]
            )

            # Create header
            row = await conn.fetchrow(
                """
                INSERT INTO stock_transfers (
                    tenant_id, transfer_number, transfer_date,
                    from_warehouse_id, to_warehouse_id,
                    expected_date, reference, notes, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING *
                """,
                ctx["tenant_id"], transfer_number, body.transfer_date,
                body.from_warehouse_id, body.to_warehouse_id,
                body.expected_date, body.reference, body.notes, ctx.get("user_id")
            )

            transfer_id = row["id"]

            # Create items
            items = []
            for idx, item in enumerate(body.items, 1):
                item_row = await conn.fetchrow(
                    """
                    INSERT INTO stock_transfer_items (
                        stock_transfer_id, item_id, item_code, item_name,
                        quantity_requested, unit, unit_cost, total_value,
                        batch_number, expiry_date, serial_numbers, line_number, notes
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    RETURNING *
                    """,
                    transfer_id, item.item_id, item.item_code, item.item_name,
                    item.quantity_requested, item.unit, item.unit_cost,
                    int(item.quantity_requested * item.unit_cost),
                    item.batch_number, item.expiry_date, item.serial_numbers,
                    idx, item.notes
                )
                items.append(StockTransferItemData(**dict(item_row)))

            # Refresh header for totals
            row = await conn.fetchrow(
                """
                SELECT st.*, fw.name as from_warehouse_name, tw.name as to_warehouse_name
                FROM stock_transfers st
                LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
                LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
                WHERE st.id = $1
                """,
                transfer_id
            )

            data = StockTransferDetailData(**dict(row), items=items)

            return CreateStockTransferResponse(data=data)


@router.patch("/{transfer_id}", response_model=StockTransferDetailResponse)
async def update_stock_transfer(request: Request, transfer_id: UUID, body: UpdateStockTransferRequest):
    """Update draft stock transfer"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            existing = await conn.fetchrow(
                "SELECT * FROM stock_transfers WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                transfer_id, ctx["tenant_id"]
            )

            if not existing:
                raise HTTPException(status_code=404, detail="Stock transfer not found")

            if existing["status"] != "draft":
                raise HTTPException(status_code=400, detail="Can only update draft transfers")

            # Update fields
            updates = []
            params = []
            param_idx = 1

            for field in ["from_warehouse_id", "to_warehouse_id", "transfer_date",
                          "expected_date", "reference", "notes"]:
                value = getattr(body, field, None)
                if value is not None:
                    updates.append(f"{field} = ${param_idx}")
                    params.append(value)
                    param_idx += 1

            if updates:
                params.extend([transfer_id])
                await conn.execute(
                    f"UPDATE stock_transfers SET {', '.join(updates)}, updated_at = NOW() WHERE id = ${param_idx}",
                    *params
                )

            # Update items if provided
            if body.items is not None:
                await conn.execute("DELETE FROM stock_transfer_items WHERE stock_transfer_id = $1", transfer_id)

                for idx, item in enumerate(body.items, 1):
                    await conn.execute(
                        """
                        INSERT INTO stock_transfer_items (
                            stock_transfer_id, item_id, item_code, item_name,
                            quantity_requested, unit, unit_cost, total_value,
                            batch_number, expiry_date, serial_numbers, line_number, notes
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                        """,
                        transfer_id, item.item_id, item.item_code, item.item_name,
                        item.quantity_requested, item.unit, item.unit_cost,
                        int(item.quantity_requested * item.unit_cost),
                        item.batch_number, item.expiry_date, item.serial_numbers,
                        idx, item.notes
                    )

            # Return updated
            return await get_stock_transfer(request, transfer_id)


@router.delete("/{transfer_id}")
async def delete_stock_transfer(request: Request, transfer_id: UUID):
    """Delete draft stock transfer"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM stock_transfers WHERE id = $1 AND tenant_id = $2",
            transfer_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Stock transfer not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only delete draft transfers")

        await conn.execute("DELETE FROM stock_transfers WHERE id = $1", transfer_id)

        return {"success": True, "message": "Stock transfer deleted"}


@router.post("/{transfer_id}/ship", response_model=ShipTransferResponse)
async def ship_stock_transfer(request: Request, transfer_id: UUID, body: ShipTransferRequest = None):
    """Ship stock transfer - reduces from_warehouse stock. NO JOURNAL ENTRY."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Use DB function
            result = await conn.fetchrow(
                "SELECT * FROM ship_stock_transfer($1, $2)",
                transfer_id, ctx.get("user_id")
            )

            if not result["success"]:
                raise HTTPException(status_code=400, detail=result["message"])

            # Get updated transfer
            row = await conn.fetchrow(
                """
                SELECT st.*, fw.name as from_warehouse_name, tw.name as to_warehouse_name
                FROM stock_transfers st
                LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
                LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
                WHERE st.id = $1
                """,
                transfer_id
            )

            return ShipTransferResponse(data=StockTransferData(**dict(row)))


@router.post("/{transfer_id}/receive", response_model=ReceiveTransferResponse)
async def receive_stock_transfer(request: Request, transfer_id: UUID, body: ReceiveTransferRequest = None):
    """Receive stock transfer - increases to_warehouse stock. NO JOURNAL ENTRY."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Convert items to JSONB if provided
            items_json = None
            if body and body.items:
                import json
                items_json = json.dumps([{"item_id": str(i.item_id), "quantity_received": float(i.quantity_received)} for i in body.items])

            result = await conn.fetchrow(
                "SELECT * FROM receive_stock_transfer($1, $2, $3::jsonb)",
                transfer_id, ctx.get("user_id"), items_json
            )

            if not result["success"]:
                raise HTTPException(status_code=400, detail=result["message"])

            row = await conn.fetchrow(
                """
                SELECT st.*, fw.name as from_warehouse_name, tw.name as to_warehouse_name
                FROM stock_transfers st
                LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
                LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
                WHERE st.id = $1
                """,
                transfer_id
            )

            return ReceiveTransferResponse(data=StockTransferData(**dict(row)))


@router.post("/{transfer_id}/cancel", response_model=CancelTransferResponse)
async def cancel_stock_transfer(request: Request, transfer_id: UUID, body: CancelTransferRequest):
    """Cancel stock transfer"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM stock_transfers WHERE id = $1 AND tenant_id = $2",
            transfer_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Stock transfer not found")

        if existing["status"] not in ["draft", "in_transit"]:
            raise HTTPException(status_code=400, detail=f"Cannot cancel transfer with status: {existing['status']}")

        # If in_transit, need to reverse the stock reduction
        if existing["status"] == "in_transit":
            items = await conn.fetch(
                "SELECT * FROM stock_transfer_items WHERE stock_transfer_id = $1",
                transfer_id
            )

            for item in items:
                # Add back to from_warehouse
                await conn.execute(
                    """
                    INSERT INTO inventory_ledger (
                        tenant_id, item_id, warehouse_id,
                        quantity_change, unit_cost, total_value,
                        source_type, source_id, transaction_date
                    ) VALUES ($1, $2, $3, $4, $5, $6, 'STOCK_TRANSFER_CANCEL', $7, CURRENT_DATE)
                    """,
                    ctx["tenant_id"], item["item_id"], existing["from_warehouse_id"],
                    item["quantity_shipped"], item["unit_cost"],
                    int(item["quantity_shipped"] * item["unit_cost"]), transfer_id
                )

        await conn.execute(
            """
            UPDATE stock_transfers
            SET status = 'cancelled',
                cancelled_at = NOW(),
                cancelled_by = $2,
                cancel_reason = $3,
                updated_at = NOW()
            WHERE id = $1
            """,
            transfer_id, ctx.get("user_id"), body.reason
        )

        return CancelTransferResponse()
