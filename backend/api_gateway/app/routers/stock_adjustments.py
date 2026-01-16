"""
Stock Adjustments Router - Inventory Adjustments with Journal

Endpoints for managing stock adjustments (penyesuaian persediaan).
Adjustments create journal entries to track inventory value changes.

Flow:
1. Create draft stock adjustment
2. Post to accounting (creates journal + updates inventory)
3. Void if needed (creates reversal journal + reverses inventory)

Endpoints:
- GET    /stock-adjustments              - List adjustments
- GET    /stock-adjustments/summary      - Summary statistics
- GET    /stock-adjustments/{id}         - Get adjustment detail
- POST   /stock-adjustments              - Create draft
- PATCH  /stock-adjustments/{id}         - Update draft
- DELETE /stock-adjustments/{id}         - Delete draft
- POST   /stock-adjustments/{id}/post    - Post to accounting
- POST   /stock-adjustments/{id}/void    - Void with reversal
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal
import uuid as uuid_module

from ..schemas.stock_adjustments import (
    CreateStockAdjustmentRequest,
    UpdateStockAdjustmentRequest,
    VoidStockAdjustmentRequest,
    StockAdjustmentResponse,
    StockAdjustmentDetailResponse,
    StockAdjustmentListResponse,
    StockAdjustmentSummaryResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
INVENTORY_ACCOUNT = "1-10400"           # Persediaan Barang Dagang
ADJUSTMENT_EXPENSE_ACCOUNT = "5-10200"  # Penyesuaian Persediaan


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None
    }


async def get_product_info(conn, tenant_id: str, product_id: UUID) -> Optional[dict]:
    """Get product info with current stock and weighted average cost."""
    # Try products table first
    product = await conn.fetchrow("""
        SELECT p.id, p.nama_produk as name, p.satuan as unit, p.barcode as code,
               COALESCE(p.purchase_price, 0) as purchase_price
        FROM products p
        WHERE p.id = $1 AND p.tenant_id = $2
    """, product_id, tenant_id)

    if not product:
        return None

    # Get current stock from persediaan
    current_stock = await conn.fetchval("""
        SELECT COALESCE(jumlah, 0) FROM persediaan
        WHERE product_id = $1 AND tenant_id = $2
    """, product_id, tenant_id) or 0

    # Get weighted average cost from inventory_ledger
    avg_cost = await conn.fetchval("""
        SELECT average_cost FROM inventory_ledger
        WHERE tenant_id = $1 AND product_id = $2
        ORDER BY created_at DESC LIMIT 1
    """, tenant_id, product_id)

    # Fall back to purchase_price if no ledger history
    if avg_cost is None or avg_cost == 0:
        avg_cost = product['purchase_price'] or 0

    return {
        "id": product['id'],
        "name": product['name'],
        "code": product['code'],
        "unit": product['unit'],
        "current_stock": float(current_stock),
        "unit_cost": int(avg_cost) if avg_cost else 0
    }


# =============================================================================
# LIST STOCK ADJUSTMENTS
# =============================================================================

@router.get("", response_model=StockAdjustmentListResponse)
async def list_stock_adjustments(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "void"]] = Query("all"),
    adjustment_type: Optional[Literal["increase", "decrease", "recount", "damaged", "expired"]] = Query(None),
    search: Optional[str] = Query(None, description="Search by number"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["adjustment_date", "adjustment_number", "total_value", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List stock adjustments with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if adjustment_type:
                conditions.append(f"adjustment_type = ${param_idx}")
                params.append(adjustment_type)
                param_idx += 1

            if search:
                conditions.append(f"adjustment_number ILIKE ${param_idx}")
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"adjustment_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"adjustment_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            valid_sorts = {
                "adjustment_date": "adjustment_date",
                "adjustment_number": "adjustment_number",
                "total_value": "total_value",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            count_query = f"SELECT COUNT(*) FROM stock_adjustments WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, adjustment_number, adjustment_date, adjustment_type,
                       storage_location_name, total_value, item_count,
                       status, reference_no, created_at
                FROM stock_adjustments
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "adjustment_number": row["adjustment_number"],
                    "adjustment_date": row["adjustment_date"].isoformat(),
                    "adjustment_type": row["adjustment_type"],
                    "storage_location_name": row["storage_location_name"],
                    "total_value": row["total_value"] or 0,
                    "item_count": row["item_count"] or 0,
                    "status": row["status"],
                    "reference_no": row["reference_no"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing stock adjustments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list stock adjustments")


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary", response_model=StockAdjustmentSummaryResponse)
async def get_stock_adjustments_summary(request: Request):
    """Get summary statistics for stock adjustments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'void') as void_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'increase') as increase_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'decrease') as decrease_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'recount') as recount_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'damaged') as damaged_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'expired') as expired_count,
                    COALESCE(SUM(total_value) FILTER (WHERE status = 'posted'), 0) as total_value
                FROM stock_adjustments
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "void_count": row["void_count"] or 0,
                    "by_type": {
                        "increase": row["increase_count"] or 0,
                        "decrease": row["decrease_count"] or 0,
                        "recount": row["recount_count"] or 0,
                        "damaged": row["damaged_count"] or 0,
                        "expired": row["expired_count"] or 0,
                    },
                    "total_value": int(row["total_value"] or 0),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stock adjustments summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET STOCK ADJUSTMENT DETAIL
# =============================================================================

@router.get("/{adjustment_id}", response_model=StockAdjustmentDetailResponse)
async def get_stock_adjustment(request: Request, adjustment_id: UUID):
    """Get detailed information for a stock adjustment."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            sa = await conn.fetchrow("""
                SELECT sa.*, je.journal_number
                FROM stock_adjustments sa
                LEFT JOIN journal_entries je ON sa.journal_id = je.id
                WHERE sa.id = $1 AND sa.tenant_id = $2
            """, adjustment_id, ctx["tenant_id"])

            if not sa:
                raise HTTPException(status_code=404, detail="Stock adjustment not found")

            items = await conn.fetch("""
                SELECT * FROM stock_adjustment_items
                WHERE stock_adjustment_id = $1
                ORDER BY line_number
            """, adjustment_id)

            return {
                "success": True,
                "data": {
                    "id": str(sa["id"]),
                    "adjustment_number": sa["adjustment_number"],
                    "adjustment_date": sa["adjustment_date"].isoformat(),
                    "adjustment_type": sa["adjustment_type"],
                    "storage_location_id": str(sa["storage_location_id"]) if sa["storage_location_id"] else None,
                    "storage_location_name": sa["storage_location_name"],
                    "reference_no": sa["reference_no"],
                    "notes": sa["notes"],
                    "total_value": sa["total_value"] or 0,
                    "item_count": sa["item_count"] or 0,
                    "status": sa["status"],
                    "journal_id": str(sa["journal_id"]) if sa["journal_id"] else None,
                    "journal_number": sa["journal_number"],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "product_id": str(item["product_id"]),
                            "product_code": item["product_code"],
                            "product_name": item["product_name"],
                            "quantity_before": float(item["quantity_before"]),
                            "quantity_adjustment": float(item["quantity_adjustment"]),
                            "quantity_after": float(item["quantity_after"]),
                            "unit": item["unit"],
                            "unit_cost": item["unit_cost"],
                            "total_value": item["total_value"],
                            "reason_detail": item["reason_detail"],
                            "system_quantity": float(item["system_quantity"]) if item["system_quantity"] else None,
                            "physical_quantity": float(item["physical_quantity"]) if item["physical_quantity"] else None,
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "posted_at": sa["posted_at"].isoformat() if sa["posted_at"] else None,
                    "posted_by": str(sa["posted_by"]) if sa["posted_by"] else None,
                    "voided_at": sa["voided_at"].isoformat() if sa["voided_at"] else None,
                    "voided_reason": sa["voided_reason"],
                    "created_at": sa["created_at"].isoformat(),
                    "updated_at": sa["updated_at"].isoformat(),
                    "created_by": str(sa["created_by"]) if sa["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stock adjustment {adjustment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get stock adjustment")


# =============================================================================
# CREATE STOCK ADJUSTMENT
# =============================================================================

@router.post("", response_model=StockAdjustmentResponse, status_code=201)
async def create_stock_adjustment(request: Request, body: CreateStockAdjustmentRequest):
    """Create a new stock adjustment in draft status."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                sa_number = await conn.fetchval(
                    "SELECT generate_stock_adjustment_number($1, 'SA')",
                    ctx["tenant_id"]
                )

                storage_location_name = None
                if body.storage_location_id:
                    loc = await conn.fetchrow(
                        "SELECT name FROM storage_locations WHERE id = $1 AND tenant_id = $2",
                        UUID(body.storage_location_id), ctx["tenant_id"]
                    )
                    if loc:
                        storage_location_name = loc["name"]

                sa_id = uuid_module.uuid4()

                await conn.execute("""
                    INSERT INTO stock_adjustments (
                        id, tenant_id, adjustment_number, adjustment_date, adjustment_type,
                        storage_location_id, storage_location_name,
                        reference_no, notes, status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'draft', $10)
                """,
                    sa_id,
                    ctx["tenant_id"],
                    sa_number,
                    body.adjustment_date,
                    body.adjustment_type,
                    UUID(body.storage_location_id) if body.storage_location_id else None,
                    storage_location_name,
                    body.reference_no,
                    body.notes,
                    ctx["user_id"]
                )

                total_value = 0
                for idx, item in enumerate(body.items, 1):
                    product = await get_product_info(conn, ctx["tenant_id"], UUID(item.product_id))
                    if not product:
                        raise HTTPException(status_code=400, detail=f"Product {item.product_id} not found")

                    quantity_before = Decimal(str(product["current_stock"]))
                    quantity_adjustment = Decimal(str(item.quantity_adjustment))
                    quantity_after = quantity_before + quantity_adjustment
                    unit_cost = product["unit_cost"]
                    item_total_value = int(abs(quantity_adjustment) * unit_cost)

                    system_quantity = None
                    physical_quantity = None
                    if body.adjustment_type == "recount" and item.physical_quantity is not None:
                        physical_quantity = Decimal(str(item.physical_quantity))
                        system_quantity = quantity_before
                        quantity_adjustment = physical_quantity - system_quantity
                        quantity_after = physical_quantity
                        item_total_value = int(abs(quantity_adjustment) * unit_cost)

                    await conn.execute("""
                        INSERT INTO stock_adjustment_items (
                            id, stock_adjustment_id, product_id, product_code, product_name,
                            quantity_before, quantity_adjustment, quantity_after, unit,
                            unit_cost, total_value, reason_detail,
                            system_quantity, physical_quantity, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        uuid_module.uuid4(),
                        sa_id,
                        UUID(item.product_id),
                        product["code"],
                        product["name"],
                        float(quantity_before),
                        float(quantity_adjustment),
                        float(quantity_after),
                        product["unit"],
                        unit_cost,
                        item_total_value,
                        item.reason_detail,
                        float(system_quantity) if system_quantity is not None else None,
                        float(physical_quantity) if physical_quantity is not None else None,
                        idx
                    )

                    total_value += item_total_value

                logger.info(f"Stock adjustment created: {sa_id}, number={sa_number}")

                return {
                    "success": True,
                    "message": "Stock adjustment created successfully",
                    "data": {
                        "id": str(sa_id),
                        "adjustment_number": sa_number,
                        "total_value": total_value,
                        "status": "draft"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating stock adjustment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create stock adjustment")


# =============================================================================
# UPDATE STOCK ADJUSTMENT
# =============================================================================

@router.patch("/{adjustment_id}", response_model=StockAdjustmentResponse)
async def update_stock_adjustment(request: Request, adjustment_id: UUID, body: UpdateStockAdjustmentRequest):
    """Update a draft stock adjustment."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                sa = await conn.fetchrow("""
                    SELECT * FROM stock_adjustments
                    WHERE id = $1 AND tenant_id = $2
                """, adjustment_id, ctx["tenant_id"])

                if not sa:
                    raise HTTPException(status_code=404, detail="Stock adjustment not found")

                if sa["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft adjustments can be updated"
                    )

                updates = []
                params = []
                param_idx = 1

                if body.adjustment_date is not None:
                    updates.append(f"adjustment_date = ${param_idx}")
                    params.append(body.adjustment_date)
                    param_idx += 1

                if body.adjustment_type is not None:
                    updates.append(f"adjustment_type = ${param_idx}")
                    params.append(body.adjustment_type)
                    param_idx += 1

                if body.reference_no is not None:
                    updates.append(f"reference_no = ${param_idx}")
                    params.append(body.reference_no)
                    param_idx += 1

                if body.notes is not None:
                    updates.append(f"notes = ${param_idx}")
                    params.append(body.notes)
                    param_idx += 1

                if body.storage_location_id is not None:
                    storage_location_name = None
                    if body.storage_location_id:
                        loc = await conn.fetchrow(
                            "SELECT name FROM storage_locations WHERE id = $1",
                            UUID(body.storage_location_id)
                        )
                        if loc:
                            storage_location_name = loc["name"]
                    updates.append(f"storage_location_id = ${param_idx}")
                    params.append(UUID(body.storage_location_id) if body.storage_location_id else None)
                    param_idx += 1
                    updates.append(f"storage_location_name = ${param_idx}")
                    params.append(storage_location_name)
                    param_idx += 1

                if updates:
                    updates.append("updated_at = NOW()")
                    params.extend([adjustment_id, ctx["tenant_id"]])

                    query = f"""
                        UPDATE stock_adjustments
                        SET {", ".join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(query, *params)

                # Update items if provided
                if body.items is not None:
                    await conn.execute(
                        "DELETE FROM stock_adjustment_items WHERE stock_adjustment_id = $1",
                        adjustment_id
                    )

                    adj_type = body.adjustment_type or sa["adjustment_type"]
                    total_value = 0

                    for idx, item in enumerate(body.items, 1):
                        product = await get_product_info(conn, ctx["tenant_id"], UUID(item.product_id))
                        if not product:
                            raise HTTPException(status_code=400, detail=f"Product {item.product_id} not found")

                        quantity_before = Decimal(str(product["current_stock"]))
                        quantity_adjustment = Decimal(str(item.quantity_adjustment))
                        quantity_after = quantity_before + quantity_adjustment
                        unit_cost = product["unit_cost"]
                        item_total_value = int(abs(quantity_adjustment) * unit_cost)

                        system_quantity = None
                        physical_quantity = None
                        if adj_type == "recount" and item.physical_quantity is not None:
                            physical_quantity = Decimal(str(item.physical_quantity))
                            system_quantity = quantity_before
                            quantity_adjustment = physical_quantity - system_quantity
                            quantity_after = physical_quantity
                            item_total_value = int(abs(quantity_adjustment) * unit_cost)

                        await conn.execute("""
                            INSERT INTO stock_adjustment_items (
                                id, stock_adjustment_id, product_id, product_code, product_name,
                                quantity_before, quantity_adjustment, quantity_after, unit,
                                unit_cost, total_value, reason_detail,
                                system_quantity, physical_quantity, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            uuid_module.uuid4(),
                            adjustment_id,
                            UUID(item.product_id),
                            product["code"],
                            product["name"],
                            float(quantity_before),
                            float(quantity_adjustment),
                            float(quantity_after),
                            product["unit"],
                            unit_cost,
                            item_total_value,
                            item.reason_detail,
                            float(system_quantity) if system_quantity is not None else None,
                            float(physical_quantity) if physical_quantity is not None else None,
                            idx
                        )

                        total_value += item_total_value

                logger.info(f"Stock adjustment updated: {adjustment_id}")

                return {
                    "success": True,
                    "message": "Stock adjustment updated successfully",
                    "data": {"id": str(adjustment_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating stock adjustment {adjustment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update stock adjustment")


# =============================================================================
# DELETE STOCK ADJUSTMENT
# =============================================================================

@router.delete("/{adjustment_id}", response_model=StockAdjustmentResponse)
async def delete_stock_adjustment(request: Request, adjustment_id: UUID):
    """Delete a draft stock adjustment."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            sa = await conn.fetchrow("""
                SELECT id, adjustment_number, status FROM stock_adjustments
                WHERE id = $1 AND tenant_id = $2
            """, adjustment_id, ctx["tenant_id"])

            if not sa:
                raise HTTPException(status_code=404, detail="Stock adjustment not found")

            if sa["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft adjustments can be deleted. Use void for posted."
                )

            await conn.execute(
                "DELETE FROM stock_adjustments WHERE id = $1",
                adjustment_id
            )

            logger.info(f"Stock adjustment deleted: {adjustment_id}")

            return {
                "success": True,
                "message": "Stock adjustment deleted",
                "data": {
                    "id": str(adjustment_id),
                    "adjustment_number": sa["adjustment_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting stock adjustment {adjustment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete stock adjustment")


# =============================================================================
# POST STOCK ADJUSTMENT
# =============================================================================

@router.post("/{adjustment_id}/post", response_model=StockAdjustmentResponse)
async def post_stock_adjustment(request: Request, adjustment_id: UUID):
    """
    Post stock adjustment to accounting.

    For INCREASE: Dr. Persediaan, Cr. Penyesuaian Persediaan
    For DECREASE: Dr. Penyesuaian Persediaan, Cr. Persediaan
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                sa = await conn.fetchrow("""
                    SELECT * FROM stock_adjustments
                    WHERE id = $1 AND tenant_id = $2
                """, adjustment_id, ctx["tenant_id"])

                if not sa:
                    raise HTTPException(status_code=404, detail="Stock adjustment not found")

                if sa["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post adjustment with status '{sa['status']}'"
                    )

                items = await conn.fetch("""
                    SELECT * FROM stock_adjustment_items
                    WHERE stock_adjustment_id = $1
                    ORDER BY line_number
                """, adjustment_id)

                if not items:
                    raise HTTPException(status_code=400, detail="No items to post")

                # Calculate totals
                total_increase = 0
                total_decrease = 0

                for item in items:
                    adj_qty = Decimal(str(item["quantity_adjustment"]))
                    item_value = item["total_value"]
                    if adj_qty > 0:
                        total_increase += item_value
                    else:
                        total_decrease += item_value

                # Get account IDs
                inventory_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], INVENTORY_ACCOUNT)

                adjustment_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], ADJUSTMENT_EXPENSE_ACCOUNT)

                if not inventory_account_id or not adjustment_account_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Required accounts not found in CoA"
                    )

                # Create journal entry
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = f"SA-{sa['adjustment_number']}"
                total_value = total_increase + total_decrease

                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'STOCK_ADJUSTMENT', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    sa["adjustment_date"],
                    f"Stock Adjustment {sa['adjustment_number']} - {sa['adjustment_type'].title()}",
                    adjustment_id,
                    str(trace_id),
                    float(total_value),
                    ctx["user_id"]
                )

                line_number = 1

                if total_increase > 0:
                    # Dr. Inventory
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                        uuid_module.uuid4(), journal_id, line_number,
                        inventory_account_id, float(total_increase),
                        f"Penambahan Persediaan - {sa['adjustment_number']}"
                    )
                    line_number += 1

                    # Cr. Adjustment Expense
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                        uuid_module.uuid4(), journal_id, line_number,
                        adjustment_account_id, float(total_increase),
                        f"Koreksi Persediaan - {sa['adjustment_number']}"
                    )
                    line_number += 1

                if total_decrease > 0:
                    # Dr. Adjustment Expense
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                        uuid_module.uuid4(), journal_id, line_number,
                        adjustment_account_id, float(total_decrease),
                        f"Penyesuaian Persediaan - {sa['adjustment_number']}"
                    )
                    line_number += 1

                    # Cr. Inventory
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                        uuid_module.uuid4(), journal_id, line_number,
                        inventory_account_id, float(total_decrease),
                        f"Pengurangan Persediaan - {sa['adjustment_number']}"
                    )

                # Update inventory for each item
                for item in items:
                    adj_qty = Decimal(str(item["quantity_adjustment"]))

                    # Update persediaan table
                    await conn.execute("""
                        UPDATE persediaan
                        SET jumlah = jumlah + $3, updated_at = NOW()
                        WHERE tenant_id = $1 AND product_id = $2
                    """, ctx["tenant_id"], item["product_id"], float(adj_qty))

                    # If no row updated, insert
                    result = await conn.fetchval("""
                        SELECT COUNT(*) FROM persediaan
                        WHERE tenant_id = $1 AND product_id = $2
                    """, ctx["tenant_id"], item["product_id"])

                    if result == 0:
                        await conn.execute("""
                            INSERT INTO persediaan (tenant_id, product_id, jumlah)
                            VALUES ($1, $2, $3)
                        """, ctx["tenant_id"], item["product_id"], float(adj_qty))

                # Update stock adjustment status
                await conn.execute("""
                    UPDATE stock_adjustments
                    SET status = 'posted', journal_id = $2,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1
                """, adjustment_id, journal_id, ctx["user_id"])

                logger.info(f"Stock adjustment posted: {adjustment_id}, journal={journal_id}")

                return {
                    "success": True,
                    "message": "Stock adjustment posted to accounting",
                    "data": {
                        "id": str(adjustment_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting stock adjustment {adjustment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post stock adjustment")


# =============================================================================
# VOID STOCK ADJUSTMENT
# =============================================================================

@router.post("/{adjustment_id}/void", response_model=StockAdjustmentResponse)
async def void_stock_adjustment(request: Request, adjustment_id: UUID, body: VoidStockAdjustmentRequest):
    """Void a stock adjustment with reversal."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                sa = await conn.fetchrow("""
                    SELECT * FROM stock_adjustments
                    WHERE id = $1 AND tenant_id = $2
                """, adjustment_id, ctx["tenant_id"])

                if not sa:
                    raise HTTPException(status_code=404, detail="Stock adjustment not found")

                if sa["status"] == "void":
                    raise HTTPException(status_code=400, detail="Stock adjustment already voided")

                if sa["status"] == "draft":
                    await conn.execute(
                        "DELETE FROM stock_adjustments WHERE id = $1",
                        adjustment_id
                    )
                    return {
                        "success": True,
                        "message": "Draft stock adjustment deleted",
                        "data": {"id": str(adjustment_id)}
                    }

                # Create reversal journal
                reversal_journal_id = None
                if sa["journal_id"]:
                    reversal_journal_id = uuid_module.uuid4()

                    original_lines = await conn.fetch("""
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """, sa["journal_id"])

                    reversal_number = f"RV-{sa['adjustment_number']}"

                    await conn.execute("""
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'STOCK_ADJUSTMENT', $5, $6, 'POSTED', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        reversal_number,
                        f"Void {sa['adjustment_number']} - {body.reason}",
                        adjustment_id,
                        sa["journal_id"],
                        float(sa["total_value"]),
                        ctx["user_id"]
                    )

                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute("""
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],
                            line["debit"],
                            f"Reversal - {line['memo'] or ''}"
                        )

                    await conn.execute("""
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                    """, sa["journal_id"], reversal_journal_id)

                # Reverse inventory changes
                items = await conn.fetch("""
                    SELECT * FROM stock_adjustment_items
                    WHERE stock_adjustment_id = $1
                """, adjustment_id)

                for item in items:
                    reversal_qty = -float(item["quantity_adjustment"])

                    await conn.execute("""
                        UPDATE persediaan
                        SET jumlah = jumlah + $3, updated_at = NOW()
                        WHERE tenant_id = $1 AND product_id = $2
                    """, ctx["tenant_id"], item["product_id"], reversal_qty)

                # Update status
                await conn.execute("""
                    UPDATE stock_adjustments
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """, adjustment_id, ctx["user_id"], body.reason)

                logger.info(f"Stock adjustment voided: {adjustment_id}")

                return {
                    "success": True,
                    "message": "Stock adjustment voided successfully",
                    "data": {
                        "id": str(adjustment_id),
                        "status": "void",
                        "reversal_journal_id": str(reversal_journal_id) if reversal_journal_id else None
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding stock adjustment {adjustment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void stock adjustment")
