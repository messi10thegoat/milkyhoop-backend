"""
Purchase Orders Router - Pesanan Pembelian

Endpoints for managing purchase orders (PO) for procurement.
IMPORTANT: PO does NOT create journal entries - journal is created only when Bill is created.

Flow:
1. Create draft PO
2. Send PO to vendor
3. Receive goods (partial or full)
4. Convert to Bill (creates journal entry via bill posting)
5. Close or Cancel PO

Status Flow:
draft -> sent -> partial_received/received -> partial_billed/billed -> closed
                                                                     ↓
                                                                cancelled

Endpoints:
- GET    /purchase-orders              - List purchase orders
- GET    /purchase-orders/pending      - Pending POs
- GET    /purchase-orders/summary      - Summary statistics
- GET    /purchase-orders/{id}         - Get PO detail
- POST   /purchase-orders              - Create draft PO
- PATCH  /purchase-orders/{id}         - Update draft/sent PO
- DELETE /purchase-orders/{id}         - Delete draft PO
- POST   /purchase-orders/{id}/send    - Mark as sent
- POST   /purchase-orders/{id}/receive - Record goods receipt
- POST   /purchase-orders/{id}/to-bill - Convert to Bill
- POST   /purchase-orders/{id}/cancel  - Cancel PO
- POST   /purchase-orders/{id}/close   - Close completed PO
- GET    /vendors/{id}/purchase-orders - POs for vendor
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal
import uuid as uuid_module

from ..schemas.purchase_orders import (
    CreatePurchaseOrderRequest,
    UpdatePurchaseOrderRequest,
    ReceiveGoodsRequest,
    ConvertToBillRequest,
    CancelPurchaseOrderRequest,
    PurchaseOrderResponse,
    PurchaseOrderDetailResponse,
    PurchaseOrderListResponse,
    PurchaseOrderSummaryResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


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


def calculate_item_totals(item: dict) -> dict:
    """Calculate item totals with discount and tax."""
    quantity = Decimal(str(item.get('quantity', 0)))
    unit_price = Decimal(str(item.get('unit_price', 0)))
    discount_percent = Decimal(str(item.get('discount_percent', 0)))
    discount_amount = Decimal(str(item.get('discount_amount', 0)))
    tax_rate = Decimal(str(item.get('tax_rate', 0)))

    subtotal = quantity * unit_price

    # Apply discount (percent takes precedence)
    if discount_percent > 0:
        discount = subtotal * discount_percent / 100
    else:
        discount = discount_amount

    after_discount = subtotal - discount

    # Apply tax
    tax_amount = after_discount * tax_rate / 100

    total = after_discount + tax_amount

    return {
        **item,
        'subtotal': int(subtotal),
        'discount_amount': int(discount),
        'tax_amount': int(tax_amount),
        'total': int(total)
    }


# =============================================================================
# LIST PURCHASE ORDERS
# =============================================================================

@router.get("", response_model=PurchaseOrderListResponse)
async def list_purchase_orders(
    request: Request,
    status: Optional[Literal["all", "draft", "sent", "partial_received", "received",
                             "partial_billed", "billed", "closed", "cancelled"]] = Query("all"),
    vendor_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by PO number or vendor name"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["po_date", "po_number", "total_amount", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List purchase orders with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(UUID(vendor_id))
                param_idx += 1

            if search:
                conditions.append(
                    f"(po_number ILIKE ${param_idx} OR vendor_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"po_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"po_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "po_date": "po_date",
                "po_number": "po_number",
                "total_amount": "total_amount",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM purchase_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, po_number, vendor_id, vendor_name,
                       po_date, expected_date, total_amount,
                       amount_received, amount_billed, status, ref_no, created_at
                FROM purchase_orders
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "po_number": row["po_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "po_date": row["po_date"].isoformat(),
                    "expected_date": row["expected_date"].isoformat() if row["expected_date"] else None,
                    "total_amount": row["total_amount"],
                    "amount_received": row["amount_received"] or 0,
                    "amount_billed": row["amount_billed"] or 0,
                    "status": row["status"],
                    "ref_no": row["ref_no"],
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
        logger.error(f"Error listing purchase orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list purchase orders")


# =============================================================================
# PENDING PURCHASE ORDERS
# =============================================================================

@router.get("/pending", response_model=PurchaseOrderListResponse)
async def list_pending_purchase_orders(
    request: Request,
    vendor_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List pending purchase orders (sent but not fully received/billed)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            conditions = [
                "tenant_id = $1",
                "status IN ('sent', 'partial_received', 'received', 'partial_billed')"
            ]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(UUID(vendor_id))
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM purchase_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, po_number, vendor_id, vendor_name,
                       po_date, expected_date, total_amount,
                       amount_received, amount_billed, status, ref_no, created_at
                FROM purchase_orders
                WHERE {where_clause}
                ORDER BY expected_date ASC NULLS LAST, po_date ASC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "po_number": row["po_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "po_date": row["po_date"].isoformat(),
                    "expected_date": row["expected_date"].isoformat() if row["expected_date"] else None,
                    "total_amount": row["total_amount"],
                    "amount_received": row["amount_received"] or 0,
                    "amount_billed": row["amount_billed"] or 0,
                    "status": row["status"],
                    "ref_no": row["ref_no"],
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
        logger.error(f"Error listing pending purchase orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list pending purchase orders")


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary", response_model=PurchaseOrderSummaryResponse)
async def get_purchase_orders_summary(request: Request):
    """Get summary statistics for purchase orders."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'sent') as sent_count,
                    COUNT(*) FILTER (WHERE status IN ('partial_received', 'received')) as received_count,
                    COUNT(*) FILTER (WHERE status IN ('partial_billed', 'billed')) as billed_count,
                    COUNT(*) FILTER (WHERE status = 'closed') as closed_count,
                    COALESCE(SUM(total_amount) FILTER (WHERE status != 'cancelled'), 0) as total_value,
                    COALESCE(SUM(amount_received) FILTER (WHERE status != 'cancelled'), 0) as total_received,
                    COALESCE(SUM(amount_billed) FILTER (WHERE status != 'cancelled'), 0) as total_billed,
                    COALESCE(SUM(total_amount - COALESCE(amount_billed, 0))
                        FILTER (WHERE status IN ('sent', 'partial_received', 'received', 'partial_billed')), 0) as pending_amount
                FROM purchase_orders
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "sent_count": row["sent_count"] or 0,
                    "received_count": row["received_count"] or 0,
                    "billed_count": row["billed_count"] or 0,
                    "closed_count": row["closed_count"] or 0,
                    "total_value": int(row["total_value"] or 0),
                    "total_received": int(row["total_received"] or 0),
                    "total_billed": int(row["total_billed"] or 0),
                    "pending_amount": int(row["pending_amount"] or 0),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting purchase orders summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET PURCHASE ORDER DETAIL
# =============================================================================

@router.get("/{po_id}", response_model=PurchaseOrderDetailResponse)
async def get_purchase_order(request: Request, po_id: UUID):
    """Get detailed information for a purchase order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get PO
            po = await conn.fetchrow("""
                SELECT * FROM purchase_orders
                WHERE id = $1 AND tenant_id = $2
            """, po_id, ctx["tenant_id"])

            if not po:
                raise HTTPException(status_code=404, detail="Purchase order not found")

            # Get items
            items = await conn.fetch("""
                SELECT * FROM purchase_order_items
                WHERE po_id = $1
                ORDER BY line_number
            """, po_id)

            # Get linked bills
            bills = await conn.fetch("""
                SELECT id, invoice_number, bill_date, grand_total, status_v2 as status
                FROM bills
                WHERE purchase_order_id = $1
                ORDER BY bill_date
            """, po_id)

            return {
                "success": True,
                "data": {
                    "id": str(po["id"]),
                    "po_number": po["po_number"],
                    "vendor_id": str(po["vendor_id"]) if po["vendor_id"] else None,
                    "vendor_name": po["vendor_name"],
                    "subtotal": po["subtotal"],
                    "discount_percent": float(po["discount_percent"] or 0),
                    "discount_amount": po["discount_amount"] or 0,
                    "tax_rate": float(po["tax_rate"] or 0),
                    "tax_amount": po["tax_amount"] or 0,
                    "total_amount": po["total_amount"],
                    "amount_received": po["amount_received"] or 0,
                    "amount_billed": po["amount_billed"] or 0,
                    "status": po["status"],
                    "po_date": po["po_date"].isoformat(),
                    "expected_date": po["expected_date"].isoformat() if po["expected_date"] else None,
                    "ship_to_address": po["ship_to_address"],
                    "ref_no": po["ref_no"],
                    "notes": po["notes"],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"]) if item["item_id"] else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "quantity_received": float(item["quantity_received"] or 0),
                            "quantity_billed": float(item["quantity_billed"] or 0),
                            "unit": item["unit"],
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"] or 0,
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"] or 0,
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "bills": [
                        {
                            "id": str(bill["id"]),
                            "invoice_number": bill["invoice_number"],
                            "bill_date": bill["bill_date"].isoformat() if bill["bill_date"] else None,
                            "grand_total": bill["grand_total"],
                            "status": bill["status"],
                        }
                        for bill in bills
                    ],
                    "sent_at": po["sent_at"].isoformat() if po["sent_at"] else None,
                    "sent_by": str(po["sent_by"]) if po["sent_by"] else None,
                    "cancelled_at": po["cancelled_at"].isoformat() if po["cancelled_at"] else None,
                    "cancelled_by": str(po["cancelled_by"]) if po["cancelled_by"] else None,
                    "cancelled_reason": po["cancelled_reason"],
                    "closed_at": po["closed_at"].isoformat() if po["closed_at"] else None,
                    "closed_by": str(po["closed_by"]) if po["closed_by"] else None,
                    "created_at": po["created_at"].isoformat(),
                    "updated_at": po["updated_at"].isoformat(),
                    "created_by": str(po["created_by"]) if po["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get purchase order")


# =============================================================================
# CREATE PURCHASE ORDER (DRAFT)
# =============================================================================

@router.post("", response_model=PurchaseOrderResponse, status_code=201)
async def create_purchase_order(request: Request, body: CreatePurchaseOrderRequest):
    """
    Create a new purchase order in draft status.

    NOTE: PO does NOT create journal entries.
    Journal entries are created when Bill is created from PO.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Generate PO number
                po_number = await conn.fetchval(
                    "SELECT generate_purchase_order_number($1, 'PO')",
                    ctx["tenant_id"]
                )

                # Calculate items and totals
                calculated_items = [calculate_item_totals(item.model_dump()) for item in body.items]
                subtotal = sum(item['subtotal'] for item in calculated_items)
                total_tax = sum(item['tax_amount'] for item in calculated_items)

                # Apply overall discount
                if body.discount_percent > 0:
                    overall_discount = int(subtotal * Decimal(str(body.discount_percent)) / 100)
                else:
                    overall_discount = body.discount_amount

                # Apply overall tax if specified
                after_discount = subtotal - overall_discount
                if body.tax_rate > 0:
                    overall_tax = int(after_discount * Decimal(str(body.tax_rate)) / 100)
                else:
                    overall_tax = total_tax

                total_amount = after_discount + overall_tax

                # Insert PO
                po_id = await conn.fetchval("""
                    INSERT INTO purchase_orders (
                        tenant_id, po_number, vendor_id, vendor_name,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, po_date, expected_date,
                        ship_to_address, ref_no, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                              'draft', $11, $12, $13, $14, $15, $16)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    po_number,
                    UUID(body.vendor_id) if body.vendor_id else None,
                    body.vendor_name,
                    subtotal,
                    body.discount_percent,
                    overall_discount,
                    body.tax_rate,
                    overall_tax,
                    total_amount,
                    body.po_date,
                    body.expected_date,
                    body.ship_to_address,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"]
                )

                # Insert items
                for idx, item in enumerate(calculated_items, 1):
                    await conn.execute("""
                        INSERT INTO purchase_order_items (
                            po_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        po_id,
                        UUID(item['item_id']) if item.get('item_id') else None,
                        item.get('item_code'),
                        item['description'],
                        item['quantity'],
                        item.get('unit'),
                        item['unit_price'],
                        item.get('discount_percent', 0),
                        item.get('discount_amount', 0),
                        item.get('tax_code'),
                        item.get('tax_rate', 0),
                        item.get('tax_amount', 0),
                        item['subtotal'],
                        item['total'],
                        idx
                    )

                logger.info(f"Purchase order created: {po_id}, number={po_number}")

                return {
                    "success": True,
                    "message": "Purchase order created successfully",
                    "data": {
                        "id": str(po_id),
                        "po_number": po_number,
                        "total_amount": total_amount,
                        "status": "draft"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating purchase order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create purchase order")


# =============================================================================
# UPDATE PURCHASE ORDER (DRAFT/SENT ONLY)
# =============================================================================

@router.patch("/{po_id}", response_model=PurchaseOrderResponse)
async def update_purchase_order(request: Request, po_id: UUID, body: UpdatePurchaseOrderRequest):
    """
    Update a purchase order.

    Only draft or sent POs can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Check status
                po = await conn.fetchrow("""
                    SELECT id, status FROM purchase_orders
                    WHERE id = $1 AND tenant_id = $2
                """, po_id, ctx["tenant_id"])

                if not po:
                    raise HTTPException(status_code=404, detail="Purchase order not found")

                if po["status"] not in ("draft", "sent"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot update PO with status '{po['status']}'"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(po_id)}
                    }

                # Handle items if provided
                if "items" in update_data and update_data["items"]:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM purchase_order_items WHERE po_id = $1",
                        po_id
                    )

                    # Calculate and insert new items
                    calculated_items = [
                        calculate_item_totals(item.model_dump())
                        for item in body.items
                    ]

                    subtotal = sum(item['subtotal'] for item in calculated_items)
                    total_tax = sum(item['tax_amount'] for item in calculated_items)

                    # Recalculate totals
                    discount_percent = update_data.get('discount_percent', 0)
                    discount_amount = update_data.get('discount_amount', 0)
                    tax_rate = update_data.get('tax_rate', 0)

                    if discount_percent > 0:
                        overall_discount = int(subtotal * Decimal(str(discount_percent)) / 100)
                    else:
                        overall_discount = discount_amount

                    after_discount = subtotal - overall_discount

                    if tax_rate > 0:
                        overall_tax = int(after_discount * Decimal(str(tax_rate)) / 100)
                    else:
                        overall_tax = total_tax

                    total_amount = after_discount + overall_tax

                    # Update totals
                    await conn.execute("""
                        UPDATE purchase_orders
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """, po_id, subtotal, overall_discount, overall_tax, total_amount)

                    # Insert new items
                    for idx, item in enumerate(calculated_items, 1):
                        await conn.execute("""
                            INSERT INTO purchase_order_items (
                                po_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            po_id,
                            UUID(item['item_id']) if item.get('item_id') else None,
                            item.get('item_code'),
                            item['description'],
                            item['quantity'],
                            item.get('unit'),
                            item['unit_price'],
                            item.get('discount_percent', 0),
                            item.get('discount_amount', 0),
                            item.get('tax_code'),
                            item.get('tax_rate', 0),
                            item.get('tax_amount', 0),
                            item['subtotal'],
                            item['total'],
                            idx
                        )

                    del update_data["items"]

                # Update other fields
                if update_data:
                    excluded = {"items"}
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field in excluded:
                            continue
                        updates.append(f"{field} = ${param_idx}")
                        if field == "vendor_id" and value:
                            params.append(UUID(value))
                        else:
                            params.append(value)
                        param_idx += 1

                    if updates:
                        params.extend([po_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE purchase_orders
                            SET {', '.join(updates)}, updated_at = NOW()
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Purchase order updated: {po_id}")

                return {
                    "success": True,
                    "message": "Purchase order updated successfully",
                    "data": {"id": str(po_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update purchase order")


# =============================================================================
# DELETE PURCHASE ORDER (DRAFT ONLY)
# =============================================================================

@router.delete("/{po_id}", response_model=PurchaseOrderResponse)
async def delete_purchase_order(request: Request, po_id: UUID):
    """
    Delete a draft purchase order.

    Only draft POs can be deleted. Use cancel for sent POs.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Check status
            po = await conn.fetchrow("""
                SELECT id, status, po_number FROM purchase_orders
                WHERE id = $1 AND tenant_id = $2
            """, po_id, ctx["tenant_id"])

            if not po:
                raise HTTPException(status_code=404, detail="Purchase order not found")

            if po["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft POs can be deleted. Use cancel for sent POs."
                )

            # Delete (cascade will delete items)
            await conn.execute(
                "DELETE FROM purchase_orders WHERE id = $1",
                po_id
            )

            logger.info(f"Purchase order deleted: {po_id}")

            return {
                "success": True,
                "message": "Purchase order deleted successfully",
                "data": {
                    "id": str(po_id),
                    "po_number": po["po_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete purchase order")


# =============================================================================
# SEND PURCHASE ORDER
# =============================================================================

@router.post("/{po_id}/send", response_model=PurchaseOrderResponse)
async def send_purchase_order(request: Request, po_id: UUID):
    """
    Mark purchase order as sent to vendor.

    Changes status from 'draft' to 'sent'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Check status
            po = await conn.fetchrow("""
                SELECT id, status, po_number FROM purchase_orders
                WHERE id = $1 AND tenant_id = $2
            """, po_id, ctx["tenant_id"])

            if not po:
                raise HTTPException(status_code=404, detail="Purchase order not found")

            if po["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot send PO with status '{po['status']}'"
                )

            # Update status
            await conn.execute("""
                UPDATE purchase_orders
                SET status = 'sent', sent_at = NOW(), sent_by = $2, updated_at = NOW()
                WHERE id = $1
            """, po_id, ctx["user_id"])

            logger.info(f"Purchase order sent: {po_id}")

            return {
                "success": True,
                "message": "Purchase order marked as sent",
                "data": {
                    "id": str(po_id),
                    "po_number": po["po_number"],
                    "status": "sent"
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send purchase order")


# =============================================================================
# RECEIVE GOODS
# =============================================================================

@router.post("/{po_id}/receive", response_model=PurchaseOrderResponse)
async def receive_goods(request: Request, po_id: UUID, body: ReceiveGoodsRequest):
    """
    Record goods receipt for a purchase order.

    Updates quantity_received for specified items.
    NOTE: This does NOT create journal entries.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get PO
                po = await conn.fetchrow("""
                    SELECT * FROM purchase_orders
                    WHERE id = $1 AND tenant_id = $2
                """, po_id, ctx["tenant_id"])

                if not po:
                    raise HTTPException(status_code=404, detail="Purchase order not found")

                if po["status"] not in ("sent", "partial_received"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot receive goods for PO with status '{po['status']}'"
                    )

                items_received = []

                for recv_item in body.items:
                    # Get PO item
                    po_item = await conn.fetchrow("""
                        SELECT * FROM purchase_order_items
                        WHERE id = $1 AND po_id = $2
                    """, UUID(recv_item.po_item_id), po_id)

                    if not po_item:
                        raise HTTPException(
                            status_code=400,
                            detail=f"PO item {recv_item.po_item_id} not found"
                        )

                    # Check quantity
                    remaining = float(po_item["quantity"]) - float(po_item["quantity_received"] or 0)
                    if recv_item.quantity_received > remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Receive quantity ({recv_item.quantity_received}) exceeds remaining ({remaining})"
                        )

                    # Update quantity_received
                    new_qty_received = float(po_item["quantity_received"] or 0) + recv_item.quantity_received

                    await conn.execute("""
                        UPDATE purchase_order_items
                        SET quantity_received = $2
                        WHERE id = $1
                    """, UUID(recv_item.po_item_id), new_qty_received)

                    items_received.append({
                        "po_item_id": recv_item.po_item_id,
                        "quantity_received": recv_item.quantity_received,
                        "total_received": new_qty_received
                    })

                # Calculate total received value and update PO
                total_received = await conn.fetchval("""
                    SELECT COALESCE(SUM(
                        (quantity_received / quantity) * total
                    ), 0)::BIGINT
                    FROM purchase_order_items
                    WHERE po_id = $1
                """, po_id)

                # Determine new status
                all_items = await conn.fetch("""
                    SELECT quantity, quantity_received FROM purchase_order_items
                    WHERE po_id = $1
                """, po_id)

                all_received = all(
                    float(item["quantity_received"] or 0) >= float(item["quantity"])
                    for item in all_items
                )

                new_status = "received" if all_received else "partial_received"

                await conn.execute("""
                    UPDATE purchase_orders
                    SET amount_received = $2, status = $3, updated_at = NOW()
                    WHERE id = $1
                """, po_id, total_received, new_status)

                logger.info(f"Goods received for PO: {po_id}, items={len(items_received)}")

                return {
                    "success": True,
                    "message": f"Goods received: {len(items_received)} items",
                    "data": {
                        "id": str(po_id),
                        "items_received": items_received,
                        "total_received": total_received,
                        "status": new_status
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error receiving goods for PO {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to receive goods")


# =============================================================================
# CONVERT TO BILL
# =============================================================================

@router.post("/{po_id}/to-bill", response_model=PurchaseOrderResponse)
async def convert_to_bill(request: Request, po_id: UUID, body: ConvertToBillRequest):
    """
    Convert purchase order to a Bill.

    Creates a Bill with items from the PO.
    NOTE: Journal entry is created when Bill is posted, not here.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get PO
                po = await conn.fetchrow("""
                    SELECT * FROM purchase_orders
                    WHERE id = $1 AND tenant_id = $2
                """, po_id, ctx["tenant_id"])

                if not po:
                    raise HTTPException(status_code=404, detail="Purchase order not found")

                billable_statuses = ("sent", "partial_received", "received", "partial_billed")
                if po["status"] not in billable_statuses:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot create bill from PO with status '{po['status']}'"
                    )

                # Get items to bill
                if body.items:
                    # Specific items
                    items_to_bill = []
                    for bill_item in body.items:
                        po_item = await conn.fetchrow("""
                            SELECT * FROM purchase_order_items
                            WHERE id = $1 AND po_id = $2
                        """, UUID(bill_item.po_item_id), po_id)

                        if not po_item:
                            raise HTTPException(
                                status_code=400,
                                detail=f"PO item {bill_item.po_item_id} not found"
                            )

                        unbilled = float(po_item["quantity"]) - float(po_item["quantity_billed"] or 0)
                        qty_to_bill = bill_item.quantity_to_bill or unbilled

                        if qty_to_bill > unbilled:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Bill quantity ({qty_to_bill}) exceeds unbilled ({unbilled})"
                            )

                        items_to_bill.append({
                            "po_item": po_item,
                            "quantity": qty_to_bill
                        })
                else:
                    # All unbilled items
                    po_items = await conn.fetch("""
                        SELECT * FROM purchase_order_items
                        WHERE po_id = $1 AND quantity > COALESCE(quantity_billed, 0)
                        ORDER BY line_number
                    """, po_id)

                    items_to_bill = [
                        {
                            "po_item": item,
                            "quantity": float(item["quantity"]) - float(item["quantity_billed"] or 0)
                        }
                        for item in po_items
                    ]

                if not items_to_bill:
                    raise HTTPException(
                        status_code=400,
                        detail="No unbilled items to bill"
                    )

                # Generate bill number
                bill_number = await conn.fetchval(
                    "SELECT generate_bill_number($1, 'BILL')",
                    ctx["tenant_id"]
                )

                if not bill_number:
                    # Fallback if function doesn't exist
                    bill_number = f"BILL-{po['po_number']}"

                # Calculate bill totals
                bill_subtotal = 0
                bill_tax = 0

                for item in items_to_bill:
                    po_item = item["po_item"]
                    qty = item["quantity"]
                    unit_price = po_item["unit_price"]

                    item_subtotal = int(qty * unit_price)

                    # Apply discount
                    if po_item["discount_percent"] and po_item["discount_percent"] > 0:
                        discount = int(item_subtotal * float(po_item["discount_percent"]) / 100)
                    else:
                        discount = int((po_item["discount_amount"] or 0) * qty / float(po_item["quantity"]))

                    after_discount = item_subtotal - discount

                    # Apply tax
                    if po_item["tax_rate"] and po_item["tax_rate"] > 0:
                        tax = int(after_discount * float(po_item["tax_rate"]) / 100)
                    else:
                        tax = 0

                    bill_subtotal += item_subtotal
                    bill_tax += tax

                bill_total = bill_subtotal - 0 + bill_tax  # No overall discount for bill from PO

                # Create Bill
                bill_id = await conn.fetchval("""
                    INSERT INTO bills (
                        tenant_id, invoice_number, vendor_id, vendor_name,
                        bill_date, due_date, amount, grand_total,
                        purchase_order_id, notes, status_v2, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'draft', $11)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    bill_number,
                    po["vendor_id"],
                    po["vendor_name"],
                    body.bill_date,
                    body.due_date,
                    bill_subtotal,
                    bill_total,
                    po_id,
                    body.notes or f"From PO {po['po_number']}",
                    ctx["user_id"]
                )

                # Create bill items and update PO items
                for idx, item in enumerate(items_to_bill, 1):
                    po_item = item["po_item"]
                    qty = item["quantity"]

                    # Calculate item totals for bill
                    unit_price = po_item["unit_price"]
                    item_subtotal = int(qty * unit_price)

                    if po_item["discount_percent"] and po_item["discount_percent"] > 0:
                        discount = int(item_subtotal * float(po_item["discount_percent"]) / 100)
                    else:
                        discount = int((po_item["discount_amount"] or 0) * qty / float(po_item["quantity"]))

                    after_discount = item_subtotal - discount

                    if po_item["tax_rate"] and po_item["tax_rate"] > 0:
                        tax = int(after_discount * float(po_item["tax_rate"]) / 100)
                    else:
                        tax = 0

                    item_total = after_discount + tax

                    # Insert bill item
                    await conn.execute("""
                        INSERT INTO bill_items (
                            bill_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        bill_id,
                        po_item["item_id"],
                        po_item["item_code"],
                        po_item["description"],
                        qty,
                        po_item["unit"],
                        unit_price,
                        po_item["discount_percent"] or 0,
                        int(discount),
                        po_item["tax_code"],
                        po_item["tax_rate"] or 0,
                        tax,
                        item_subtotal,
                        item_total,
                        idx
                    )

                    # Update PO item quantity_billed
                    new_qty_billed = float(po_item["quantity_billed"] or 0) + qty
                    await conn.execute("""
                        UPDATE purchase_order_items
                        SET quantity_billed = $2
                        WHERE id = $1
                    """, po_item["id"], new_qty_billed)

                # Update PO amount_billed and status
                total_billed = await conn.fetchval("""
                    SELECT COALESCE(SUM(
                        (quantity_billed / quantity) * total
                    ), 0)::BIGINT
                    FROM purchase_order_items
                    WHERE po_id = $1
                """, po_id)

                # Determine new status
                all_items = await conn.fetch("""
                    SELECT quantity, quantity_billed FROM purchase_order_items
                    WHERE po_id = $1
                """, po_id)

                all_billed = all(
                    float(item["quantity_billed"] or 0) >= float(item["quantity"])
                    for item in all_items
                )

                new_status = "billed" if all_billed else "partial_billed"

                await conn.execute("""
                    UPDATE purchase_orders
                    SET amount_billed = $2, status = $3, updated_at = NOW()
                    WHERE id = $1
                """, po_id, total_billed, new_status)

                logger.info(f"Bill created from PO: {po_id}, bill={bill_id}")

                return {
                    "success": True,
                    "message": "Bill created from purchase order",
                    "data": {
                        "id": str(po_id),
                        "bill_id": str(bill_id),
                        "bill_number": bill_number,
                        "bill_total": bill_total,
                        "po_status": new_status
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting PO to bill {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to convert to bill")


# =============================================================================
# CANCEL PURCHASE ORDER
# =============================================================================

@router.post("/{po_id}/cancel", response_model=PurchaseOrderResponse)
async def cancel_purchase_order(request: Request, po_id: UUID, body: CancelPurchaseOrderRequest):
    """
    Cancel a purchase order.

    Can only cancel if no goods received and no bills created.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get PO
            po = await conn.fetchrow("""
                SELECT * FROM purchase_orders
                WHERE id = $1 AND tenant_id = $2
            """, po_id, ctx["tenant_id"])

            if not po:
                raise HTTPException(status_code=404, detail="Purchase order not found")

            if po["status"] in ("cancelled", "closed"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel PO with status '{po['status']}'"
                )

            # Check for received goods
            if (po["amount_received"] or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot cancel PO with received goods"
                )

            # Check for bills
            if (po["amount_billed"] or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot cancel PO with bills"
                )

            # Update status
            await conn.execute("""
                UPDATE purchase_orders
                SET status = 'cancelled', cancelled_at = NOW(),
                    cancelled_by = $2, cancelled_reason = $3, updated_at = NOW()
                WHERE id = $1
            """, po_id, ctx["user_id"], body.reason)

            logger.info(f"Purchase order cancelled: {po_id}")

            return {
                "success": True,
                "message": "Purchase order cancelled",
                "data": {
                    "id": str(po_id),
                    "status": "cancelled"
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel purchase order")


# =============================================================================
# CLOSE PURCHASE ORDER
# =============================================================================

@router.post("/{po_id}/close", response_model=PurchaseOrderResponse)
async def close_purchase_order(request: Request, po_id: UUID):
    """
    Close a completed purchase order.

    Marks the PO as completed.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get PO
            po = await conn.fetchrow("""
                SELECT * FROM purchase_orders
                WHERE id = $1 AND tenant_id = $2
            """, po_id, ctx["tenant_id"])

            if not po:
                raise HTTPException(status_code=404, detail="Purchase order not found")

            if po["status"] in ("draft", "cancelled", "closed"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot close PO with status '{po['status']}'"
                )

            # Update status
            await conn.execute("""
                UPDATE purchase_orders
                SET status = 'closed', closed_at = NOW(),
                    closed_by = $2, updated_at = NOW()
                WHERE id = $1
            """, po_id, ctx["user_id"])

            logger.info(f"Purchase order closed: {po_id}")

            return {
                "success": True,
                "message": "Purchase order closed",
                "data": {
                    "id": str(po_id),
                    "status": "closed"
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close purchase order")


# =============================================================================
# LIST PURCHASE ORDERS FOR VENDOR
# =============================================================================

@router.get("/vendor/{vendor_id}", response_model=PurchaseOrderListResponse)
async def list_purchase_orders_by_vendor(
    request: Request,
    vendor_id: UUID,
    status: Optional[str] = Query("all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List purchase orders for a specific vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["tenant_id = $1", "vendor_id = $2"]
            params = [ctx["tenant_id"], vendor_id]
            param_idx = 3

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM purchase_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, po_number, vendor_id, vendor_name,
                       po_date, expected_date, total_amount,
                       amount_received, amount_billed, status, ref_no, created_at
                FROM purchase_orders
                WHERE {where_clause}
                ORDER BY po_date DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "po_number": row["po_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "po_date": row["po_date"].isoformat(),
                    "expected_date": row["expected_date"].isoformat() if row["expected_date"] else None,
                    "total_amount": row["total_amount"],
                    "amount_received": row["amount_received"] or 0,
                    "amount_billed": row["amount_billed"] or 0,
                    "status": row["status"],
                    "ref_no": row["ref_no"],
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
        logger.error(f"Error listing POs for vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list purchase orders")
