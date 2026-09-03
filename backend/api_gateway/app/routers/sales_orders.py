"""
Sales Orders Router
Order management with shipment tracking.
NO journal entries - accounting impact happens on Invoice creation.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from datetime import date
from decimal import Decimal
import asyncpg
import logging
import uuid as uuid_module

from ..schemas.sales_orders import (
    CreateSalesOrderRequest,
    UpdateSalesOrderRequest,
    CreateShipmentRequest,
    ConvertToInvoiceRequest,
    CancelSalesOrderRequest,
    SalesOrderListResponse,
    SalesOrderDetailResponse,
    SalesOrderResponse,
    SalesOrderSummaryResponse,
    PendingOrdersResponse,
    SalesOrderListItem,
    SalesOrderDetail,
    SalesOrderDepositSummary,
    SalesOrderItemResponse,
    ShipmentDetail,
    ShipmentItemResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": uuid_module.UUID(user_id) if user_id else None,
    }


def calculate_item_totals(item: dict) -> dict:
    """Calculate line item totals."""
    quantity = Decimal(str(item.get("quantity", 1)))
    unit_price = Decimal(str(item.get("unit_price", 0)))
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

    subtotal = quantity * unit_price
    discount = subtotal * discount_percent / 100
    after_discount = subtotal - discount
    tax_amount = after_discount * tax_rate / 100
    line_total = after_discount + tax_amount

    return {**item, "tax_amount": int(tax_amount), "line_total": int(line_total)}


def calculate_order_totals(
    items: list, discount_amount: int, shipping_amount: int
) -> dict:
    """Calculate order totals from items."""
    subtotal = sum(
        item.get("line_total", 0) - item.get("tax_amount", 0) for item in items
    )
    total_tax = sum(item.get("tax_amount", 0) for item in items)
    total_amount = subtotal - discount_amount + total_tax + shipping_amount

    return {"subtotal": subtotal, "tax_amount": total_tax, "total_amount": total_amount}


# ============================================================================
# LIST & DETAIL ENDPOINTS
# ============================================================================


@router.get("", response_model=SalesOrderListResponse)
async def list_sales_orders(
    request: Request,
    status: Optional[
        Literal[
            "all",
            "draft",
            "confirmed",
            "partial_shipped",
            "shipped",
            "partial_invoiced",
            "invoiced",
            "completed",
            "cancelled",
        ]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List sales orders with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(uuid_module.UUID(customer_id))
                param_idx += 1

            if start_date:
                conditions.append(f"order_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"order_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(order_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(order_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM sales_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            list_query = f"""
                SELECT id, order_number, order_date, expected_ship_date, customer_id, customer_name,
                       subtotal, discount_amount, tax_amount, shipping_amount, total_amount,
                       status, shipped_qty, invoiced_qty, created_at
                FROM sales_orders
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(list_query, *params)

            items = [
                SalesOrderListItem(
                    id=str(row["id"]),
                    order_number=row["order_number"],
                    order_date=row["order_date"].isoformat(),
                    expected_ship_date=row["expected_ship_date"].isoformat()
                    if row["expected_ship_date"]
                    else None,
                    customer_id=str(row["customer_id"]),
                    customer_name=row["customer_name"],
                    subtotal=row["subtotal"],
                    discount_amount=row["discount_amount"],
                    tax_amount=row["tax_amount"],
                    shipping_amount=row["shipping_amount"],
                    total_amount=row["total_amount"],
                    status=row["status"],
                    shipped_qty=float(row["shipped_qty"] or 0),
                    invoiced_qty=float(row["invoiced_qty"] or 0),
                    created_at=row["created_at"].isoformat(),
                )
                for row in rows
            ]

            return SalesOrderListResponse(
                items=items, total=total, has_more=(skip + limit) < total
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sales orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list sales orders")


@router.get("/pending", response_model=PendingOrdersResponse)
async def get_pending_orders(
    request: Request, action: Literal["shipment", "invoice", "all"] = Query("all")
):
    """Get orders pending shipment or invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            if action == "shipment":
                statuses = ["confirmed", "partial_shipped"]
            elif action == "invoice":
                statuses = ["shipped", "partial_shipped", "partial_invoiced"]
            else:
                statuses = [
                    "confirmed",
                    "partial_shipped",
                    "shipped",
                    "partial_invoiced",
                ]

            query = """
                SELECT so.id, so.order_number, so.customer_name, so.order_date, so.total_amount, so.status,
                       COALESCE(SUM(soi.quantity - soi.quantity_shipped), 0) as pending_ship,
                       COALESCE(SUM(soi.quantity - soi.quantity_invoiced), 0) as pending_invoice
                FROM sales_orders so
                LEFT JOIN sales_order_items soi ON so.id = soi.sales_order_id
                WHERE so.tenant_id = $1 AND so.status = ANY($2)
                GROUP BY so.id
                ORDER BY so.order_date ASC
            """
            rows = await conn.fetch(query, ctx["tenant_id"], statuses)

            items = []
            for row in rows:
                pending_qty = (
                    float(row["pending_ship"])
                    if row["status"] in ["confirmed", "partial_shipped"]
                    else float(row["pending_invoice"])
                )
                pending_action = (
                    "shipment"
                    if row["status"] in ["confirmed", "partial_shipped"]
                    else "invoice"
                )

                items.append(
                    {
                        "id": str(row["id"]),
                        "order_number": row["order_number"],
                        "customer_name": row["customer_name"],
                        "order_date": row["order_date"].isoformat(),
                        "total_amount": row["total_amount"],
                        "status": row["status"],
                        "pending_qty": pending_qty,
                        "pending_action": pending_action,
                    }
                )

            return PendingOrdersResponse(success=True, data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting pending orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get pending orders")


@router.get("/summary", response_model=SalesOrderSummaryResponse)
async def get_sales_order_summary(request: Request):
    """Get sales order statistics summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total_orders,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'confirmed') as confirmed_count,
                    COUNT(*) FILTER (WHERE status = 'partial_shipped') as partial_shipped_count,
                    COUNT(*) FILTER (WHERE status = 'shipped') as shipped_count,
                    COUNT(*) FILTER (WHERE status = 'partial_invoiced') as partial_invoiced_count,
                    COUNT(*) FILTER (WHERE status = 'invoiced') as invoiced_count,
                    COUNT(*) FILTER (WHERE status = 'completed') as completed_count,
                    COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled_count,
                    COALESCE(SUM(total_amount), 0) as total_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status IN ('confirmed', 'partial_shipped')), 0) as pending_shipment_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status IN ('shipped', 'partial_invoiced')), 0) as pending_invoice_value
                FROM sales_orders
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return SalesOrderSummaryResponse(
                success=True,
                data={
                    "total_orders": row["total_orders"],
                    "draft_count": row["draft_count"],
                    "confirmed_count": row["confirmed_count"],
                    "partial_shipped_count": row["partial_shipped_count"],
                    "shipped_count": row["shipped_count"],
                    "partial_invoiced_count": row["partial_invoiced_count"],
                    "invoiced_count": row["invoiced_count"],
                    "completed_count": row["completed_count"],
                    "cancelled_count": row["cancelled_count"],
                    "total_value": row["total_value"],
                    "pending_shipment_value": row["pending_shipment_value"],
                    "pending_invoice_value": row["pending_invoice_value"],
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sales order summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get sales order summary")


@router.get("/{order_id}", response_model=SalesOrderDetailResponse)
async def get_sales_order_detail(request: Request, order_id: str):
    """Get sales order detail with items and shipments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get order header (G4: join quotes for human-readable quote_number)
            order = await conn.fetchrow(
                """
                SELECT so.*, q.quote_number
                FROM sales_orders so
                LEFT JOIN quotes q ON q.id = so.quote_id
                WHERE so.id = $1 AND so.tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM sales_order_items WHERE sales_order_id = $1 ORDER BY sort_order, id
            """,
                uuid_module.UUID(order_id),
            )

            # Get shipments
            shipments = await conn.fetch(
                """
                SELECT * FROM sales_order_shipments WHERE sales_order_id = $1 ORDER BY shipment_date DESC
            """,
                uuid_module.UUID(order_id),
            )

            shipment_details = []
            for shp in shipments:
                shp_items = await conn.fetch(
                    """
                    SELECT si.*, soi.description
                    FROM sales_order_shipment_items si
                    JOIN sales_order_items soi ON si.sales_order_item_id = soi.id
                    WHERE si.shipment_id = $1
                """,
                    shp["id"],
                )

                shipment_details.append(
                    ShipmentDetail(
                        id=str(shp["id"]),
                        shipment_number=shp["shipment_number"],
                        shipment_date=shp["shipment_date"].isoformat(),
                        carrier=shp["carrier"],
                        tracking_number=shp["tracking_number"],
                        status=shp["status"],
                        items=[
                            ShipmentItemResponse(
                                id=str(si["id"]),
                                sales_order_item_id=str(si["sales_order_item_id"]),
                                description=si["description"],
                                quantity_shipped=float(si["quantity_shipped"]),
                            )
                            for si in shp_items
                        ],
                        created_at=shp["created_at"].isoformat(),
                        shipped_at=shp["shipped_at"].isoformat()
                        if shp["shipped_at"]
                        else None,
                        delivered_at=shp["delivered_at"].isoformat()
                        if shp["delivered_at"]
                        else None,
                    )
                )

            # Get related invoices
            invoices = await conn.fetch(
                """
                SELECT id, invoice_number, invoice_date, total_amount, status
                FROM sales_invoices WHERE sales_order_id = $1
            """,
                uuid_module.UUID(order_id),
            )

            # G2: linked customer deposits (exclude void)
            deposits = await conn.fetch(
                """
                SELECT id, deposit_number, amount, status
                FROM customer_deposits
                WHERE sales_order_id = $1 AND tenant_id = $2 AND status <> 'void'
                ORDER BY created_at
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            return SalesOrderDetailResponse(
                success=True,
                data=SalesOrderDetail(
                    id=str(order["id"]),
                    order_number=order["order_number"],
                    order_date=order["order_date"].isoformat(),
                    expected_ship_date=order["expected_ship_date"].isoformat()
                    if order["expected_ship_date"]
                    else None,
                    customer_id=str(order["customer_id"]),
                    customer_name=order["customer_name"],
                    quote_id=str(order["quote_id"]) if order["quote_id"] else None,
                    quote_number=order["quote_number"],
                    reference=order["reference"],
                    shipping_address=order["shipping_address"],
                    shipping_method=order["shipping_method"],
                    subtotal=order["subtotal"],
                    discount_amount=order["discount_amount"],
                    tax_amount=order["tax_amount"],
                    shipping_amount=order["shipping_amount"],
                    total_amount=order["total_amount"],
                    status=order["status"],
                    shipped_qty=float(order["shipped_qty"] or 0),
                    invoiced_qty=float(order["invoiced_qty"] or 0),
                    notes=order["notes"],
                    internal_notes=order["internal_notes"],
                    # T199: syarat DP yang dibawa dari Penawaran (V224).
                    dp_percent=order["dp_percent"],
                    dp_amount=order["dp_amount"],
                    payment_terms=order["payment_terms"],
                    payment_bank_name=order["payment_bank_name"],
                    payment_account_number=order["payment_account_number"],
                    payment_account_holder=order["payment_account_holder"],
                    items=[
                        SalesOrderItemResponse(
                            id=str(item["id"]),
                            item_id=str(item["item_id"]) if item["item_id"] else None,
                            description=item["description"],
                            quantity=float(item["quantity"]),
                            quantity_shipped=float(item["quantity_shipped"]),
                            quantity_invoiced=float(item["quantity_invoiced"]),
                            quantity_remaining=float(
                                item["quantity"] - item["quantity_shipped"]
                            ),
                            unit=item["unit"],
                            unit_price=item["unit_price"],
                            discount_percent=float(item["discount_percent"]),
                            tax_id=str(item["tax_id"]) if item["tax_id"] else None,
                            tax_rate=float(item["tax_rate"]),
                            tax_amount=item["tax_amount"],
                            line_total=item["line_total"],
                            warehouse_id=str(item["warehouse_id"])
                            if item["warehouse_id"]
                            else None,
                            sort_order=item["sort_order"],
                        )
                        for item in items
                    ],
                    shipments=shipment_details,
                    invoices=[
                        {
                            "id": str(inv["id"]),
                            "invoice_number": inv["invoice_number"],
                            "invoice_date": inv["invoice_date"].isoformat(),
                            "total_amount": inv["total_amount"],
                            "status": inv["status"],
                        }
                        for inv in invoices
                    ],
                    deposits=[
                        SalesOrderDepositSummary(
                            id=str(dep["id"]),
                            deposit_number=dep["deposit_number"],
                            amount=dep["amount"],
                            status=dep["status"],
                        )
                        for dep in deposits
                    ],
                    created_at=order["created_at"].isoformat(),
                    updated_at=order["updated_at"].isoformat(),
                    created_by=str(order["created_by"])
                    if order["created_by"]
                    else None,
                    confirmed_at=order["confirmed_at"].isoformat()
                    if order["confirmed_at"]
                    else None,
                    confirmed_by=str(order["confirmed_by"])
                    if order["confirmed_by"]
                    else None,
                ),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sales order detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get sales order detail")


# ============================================================================
# CREATE, UPDATE, DELETE ENDPOINTS
# ============================================================================


@router.post("", response_model=SalesOrderResponse)
async def create_sales_order(request: Request, body: CreateSalesOrderRequest):
    """Create a new sales order (draft status)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                order_number = await conn.fetchval(
                    "SELECT generate_sales_order_number($1, 'SO')", ctx["tenant_id"]
                )

                calculated_items = [
                    calculate_item_totals(item.model_dump()) for item in body.items
                ]
                totals = calculate_order_totals(
                    calculated_items, body.discount_amount, body.shipping_amount
                )

                # Auto-resolve customer_name if not provided
                if not body.customer_name and body.customer_id:
                    cust = await conn.fetchrow(
                        "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2",
                        uuid_module.UUID(body.customer_id),
                        ctx["tenant_id"],
                    )
                    if cust:
                        body.customer_name = cust["nama"]

                order_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO sales_orders (
                        id, tenant_id, order_number, order_date, expected_ship_date,
                        customer_id, customer_name, quote_id, reference,
                        shipping_address, shipping_method,
                        subtotal, discount_amount, tax_amount, shipping_amount, total_amount,
                        status, notes, internal_notes, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, 'draft', $17, $18, $19
                    )
                """,
                    order_id,
                    ctx["tenant_id"],
                    order_number,
                    body.order_date,
                    body.expected_ship_date,
                    uuid_module.UUID(body.customer_id),
                    body.customer_name,
                    uuid_module.UUID(body.quote_id) if body.quote_id else None,
                    body.reference,
                    body.shipping_address,
                    body.shipping_method,
                    totals["subtotal"],
                    body.discount_amount,
                    totals["tax_amount"],
                    body.shipping_amount,
                    totals["total_amount"],
                    body.notes,
                    body.internal_notes,
                    ctx["user_id"],
                )

                for idx, item in enumerate(calculated_items):
                    await conn.execute(
                        """
                        INSERT INTO sales_order_items (
                            id, sales_order_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total,
                            warehouse_id, sort_order
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                        uuid_module.uuid4(),
                        order_id,
                        uuid_module.UUID(item["item_id"])
                        if item.get("item_id")
                        else None,
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        uuid_module.UUID(item["tax_id"])
                        if item.get("tax_id")
                        else None,
                        item.get("tax_rate", 0),
                        item["tax_amount"],
                        item["line_total"],
                        uuid_module.UUID(item["warehouse_id"])
                        if item.get("warehouse_id")
                        else None,
                        item.get("sort_order", idx),
                    )

                return SalesOrderResponse(
                    success=True,
                    message="Sales order created successfully",
                    data={"id": str(order_id), "order_number": order_number},
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create sales order")


@router.patch("/{order_id}", response_model=SalesOrderResponse)
async def update_sales_order(
    request: Request, order_id: str, body: UpdateSalesOrderRequest
):
    """Update a sales order (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    """
                    SELECT id, status FROM sales_orders WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(order_id),
                    ctx["tenant_id"],
                )

                if not order:
                    raise HTTPException(status_code=404, detail="Sales order not found")

                if order["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft orders can be updated"
                    )

                updates = []
                params = []
                param_idx = 1

                update_fields = {
                    "order_date": body.order_date,
                    "expected_ship_date": body.expected_ship_date,
                    "customer_id": uuid_module.UUID(body.customer_id)
                    if body.customer_id
                    else None,
                    "customer_name": body.customer_name,
                    "reference": body.reference,
                    "shipping_address": body.shipping_address,
                    "shipping_method": body.shipping_method,
                    "shipping_amount": body.shipping_amount,
                    "discount_amount": body.discount_amount,
                    "notes": body.notes,
                    "internal_notes": body.internal_notes,
                    # T199: syarat DP boleh diubah selama SO masih draft.
                    "dp_percent": body.dp_percent,
                    "dp_amount": body.dp_amount,
                    "payment_terms": body.payment_terms,
                    "payment_bank_name": body.payment_bank_name,
                    "payment_account_number": body.payment_account_number,
                    "payment_account_holder": body.payment_account_holder,
                }

                for field, value in update_fields.items():
                    if value is not None:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                if body.items is not None:
                    await conn.execute(
                        "DELETE FROM sales_order_items WHERE sales_order_id = $1",
                        uuid_module.UUID(order_id),
                    )

                    calculated_items = [
                        calculate_item_totals(item.model_dump()) for item in body.items
                    ]
                    discount_amt = (
                        body.discount_amount if body.discount_amount is not None else 0
                    )
                    shipping_amt = (
                        body.shipping_amount if body.shipping_amount is not None else 0
                    )

                    if body.discount_amount is None or body.shipping_amount is None:
                        current = await conn.fetchrow(
                            "SELECT discount_amount, shipping_amount FROM sales_orders WHERE id = $1",
                            uuid_module.UUID(order_id),
                        )
                        discount_amt = (
                            body.discount_amount
                            if body.discount_amount is not None
                            else current["discount_amount"]
                        )
                        shipping_amt = (
                            body.shipping_amount
                            if body.shipping_amount is not None
                            else current["shipping_amount"]
                        )

                    totals = calculate_order_totals(
                        calculated_items, discount_amt, shipping_amt
                    )

                    for fld, val in [
                        ("subtotal", totals["subtotal"]),
                        ("tax_amount", totals["tax_amount"]),
                        ("total_amount", totals["total_amount"]),
                    ]:
                        updates.append(f"{fld} = ${param_idx}")
                        params.append(val)
                        param_idx += 1

                    for idx, item in enumerate(calculated_items):
                        await conn.execute(
                            """
                            INSERT INTO sales_order_items (
                                id, sales_order_id, item_id, description,
                                quantity, unit, unit_price, discount_percent,
                                tax_id, tax_rate, tax_amount, line_total,
                                warehouse_id, sort_order
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                        """,
                            uuid_module.uuid4(),
                            uuid_module.UUID(order_id),
                            uuid_module.UUID(item["item_id"])
                            if item.get("item_id")
                            else None,
                            item["description"],
                            item["quantity"],
                            item.get("unit"),
                            item["unit_price"],
                            item.get("discount_percent", 0),
                            uuid_module.UUID(item["tax_id"])
                            if item.get("tax_id")
                            else None,
                            item.get("tax_rate", 0),
                            item["tax_amount"],
                            item["line_total"],
                            uuid_module.UUID(item["warehouse_id"])
                            if item.get("warehouse_id")
                            else None,
                            item.get("sort_order", idx),
                        )

                if updates:
                    params.append(uuid_module.UUID(order_id))
                    params.append(ctx["tenant_id"])
                    await conn.execute(
                        f"""
                        UPDATE sales_orders SET {', '.join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """,
                        *params,
                    )

                return SalesOrderResponse(
                    success=True, message="Sales order updated", data={"id": order_id}
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update sales order")


@router.delete("/{order_id}", response_model=SalesOrderResponse)
async def delete_sales_order(request: Request, order_id: str):
    """Delete a sales order (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id, status, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            if order["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft orders can be deleted"
                )

            await conn.execute(
                "DELETE FROM sales_orders WHERE id = $1", uuid_module.UUID(order_id)
            )

            return SalesOrderResponse(
                success=True,
                message="Sales order deleted",
                data={"order_number": order["order_number"]},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete sales order")


# ============================================================================
# WORKFLOW ENDPOINTS
# ============================================================================


@router.post("/{order_id}/confirm", response_model=SalesOrderResponse)
async def confirm_sales_order(request: Request, order_id: str):
    """Confirm a sales order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id, status, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            if order["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot confirm order with status '{order['status']}'",
                )

            await conn.execute(
                """
                UPDATE sales_orders SET status = 'confirmed', confirmed_at = NOW(), confirmed_by = $3
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
                ctx["user_id"],
            )

            return SalesOrderResponse(
                success=True,
                message="Sales order confirmed",
                data={"order_number": order["order_number"], "status": "confirmed"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to confirm sales order")


@router.post("/{order_id}/cancel", response_model=SalesOrderResponse)
async def cancel_sales_order(
    request: Request, order_id: str, body: CancelSalesOrderRequest = None
):
    """Cancel a sales order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id, status, order_number, shipped_qty, invoiced_qty FROM sales_orders
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            if order["status"] in ("cancelled", "completed", "invoiced"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel order with status '{order['status']}'",
                )

            if order["shipped_qty"] > 0 or order["invoiced_qty"] > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot cancel order with shipments or invoices",
                )

            await conn.execute(
                """
                UPDATE sales_orders SET status = 'cancelled'
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            return SalesOrderResponse(
                success=True,
                message="Sales order cancelled",
                data={"order_number": order["order_number"], "status": "cancelled"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel sales order")


@router.post("/{order_id}/close", response_model=SalesOrderResponse)
async def close_sales_order(request: Request, order_id: str):
    """Close a completed sales order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id, status, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            if order["status"] not in ("invoiced", "shipped"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot close order with status '{order['status']}'",
                )

            await conn.execute(
                """
                UPDATE sales_orders SET status = 'completed'
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            return SalesOrderResponse(
                success=True,
                message="Sales order closed",
                data={"order_number": order["order_number"], "status": "completed"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close sales order")


# ============================================================================
# SHIPMENT ENDPOINTS
# ============================================================================


@router.post("/{order_id}/ship", response_model=SalesOrderResponse)
async def create_shipment(request: Request, order_id: str, body: CreateShipmentRequest):
    """DINONAKTIFKAN — keputusan K2 (rencana Proforma, butir 3.4.5).

    Jalur penyerahan barang di MilkyHoop adalah JALUR TUNGGAL:
    `invoice_fulfillments` (buat/pakai faktur penjualan, lalu catat penyerahan
    lewat POST /api/sales-invoices/{invoice_id}/fulfill). Jalur itulah yang
    mengakui penyerahan, pendapatan, DAN HPP dengan jurnal seimbang.

    Jalur Sales Order (`sales_order_shipments`) TIDAK PERNAH dieksekusi di
    produksi (0 baris) dan TIDAK menjurnal apa pun — stok keluar tanpa HPP,
    pendapatan tak diakui. Dua jalur penyerahan yang hidup berdampingan =
    pembukuan bercabang; sistem ini sudah punya dua presedennya (dua sumber
    navigasi, dua sumber status PKP).

    Endpoint SENGAJA TIDAK DIHAPUS: pemanggil lama harus menerima pesan yang
    menunjuk jalur pengganti, bukan 404 yang membingungkan.

    HTTP 409 Conflict dipilih (bukan 400/410/501): permintaannya sendiri sah
    dan terbentuk benar, yang bertabrakan adalah ATURAN BISNIS — hanya boleh
    ada satu jalur penyerahan. 410 Gone salah karena endpoint masih ada dan
    ada penggantinya; 501 salah karena fitur ini pernah diimplementasikan,
    bukan belum dibuat; 400 salah karena tak ada yang keliru pada payload.

    ⚠️ JANGAN "diperbaiki" balik menjadi jalur tulis. Menghidupkan kembali blok
    INSERT ke sales_order_shipments = membelah pembukuan. Lihat keputusan K2.
    Tabel `sales_order_shipments` sengaja TIDAK di-DROP (0 baris, tak ada data
    hilang, tapi menghapus tabel produksi berisiko tanpa manfaat), dan
    GET /{order_id}/shipments TETAP HIDUP untuk membaca riwayat.
    """
    raise HTTPException(
        status_code=409,
        detail=(
            "Pencatatan pengiriman lewat Sales Order sudah dinonaktifkan. "
            "Gunakan jalur resmi: buat atau pakai Faktur Penjualan untuk pesanan ini, "
            "lalu catat penyerahan barang lewat faktur tersebut "
            "(POST /api/sales-invoices/{invoice_id}/fulfill) sehingga penyerahan, "
            "pendapatan, dan HPP tercatat dalam satu jurnal yang seimbang."
        ),
    )


@router.get("/{order_id}/shipments")
async def get_order_shipments(request: Request, order_id: str):
    """Get all shipments for an order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            shipments = await conn.fetch(
                """
                SELECT * FROM sales_order_shipments WHERE sales_order_id = $1 ORDER BY shipment_date DESC
            """,
                uuid_module.UUID(order_id),
            )

            result = []
            for shp in shipments:
                items = await conn.fetch(
                    """
                    SELECT si.*, soi.description
                    FROM sales_order_shipment_items si
                    JOIN sales_order_items soi ON si.sales_order_item_id = soi.id
                    WHERE si.shipment_id = $1
                """,
                    shp["id"],
                )

                result.append(
                    {
                        "id": str(shp["id"]),
                        "shipment_number": shp["shipment_number"],
                        "shipment_date": shp["shipment_date"].isoformat(),
                        "carrier": shp["carrier"],
                        "tracking_number": shp["tracking_number"],
                        "status": shp["status"],
                        "items": [
                            {
                                "id": str(i["id"]),
                                "description": i["description"],
                                "quantity_shipped": float(i["quantity_shipped"]),
                            }
                            for i in items
                        ],
                    }
                )

            return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting shipments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get shipments")


# ============================================================================
# CONVERSION ENDPOINTS
# ============================================================================


def _rek_eksplisit(body, nama: str):
    """Nilai rekening yang dikirim EKSPLISIT di body convert, kalau ada.

    `body` boleh None (kedua endpoint convert memberinya default None), dan
    string kosong diperlakukan sama dengan tidak mengirim -- keduanya jatuh ke
    nilai warisan lewat `or` di sisi pemanggil.
    """
    nilai = getattr(body, nama, None) if body is not None else None
    if isinstance(nilai, str):
        nilai = nilai.strip() or None
    return nilai



@router.post("/{order_id}/to-invoice", response_model=SalesOrderResponse)
async def convert_to_invoice(
    request: Request, order_id: str, body: ConvertToInvoiceRequest = None
):
    """Convert sales order to invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    """
                    SELECT * FROM sales_orders WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(order_id),
                    ctx["tenant_id"],
                )

                if not order:
                    raise HTTPException(status_code=404, detail="Sales order not found")

                if order["status"] in ("draft", "cancelled", "invoiced", "completed"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot invoice order with status '{order['status']}'",
                    )

                # Get items to invoice
                if body and body.items:
                    # Partial invoice with specific quantities
                    items_to_invoice = []
                    for inv_item in body.items:
                        soi = await conn.fetchrow(
                            """
                            SELECT * FROM sales_order_items WHERE id = $1 AND sales_order_id = $2
                        """,
                            uuid_module.UUID(inv_item["so_item_id"]),
                            uuid_module.UUID(order_id),
                        )

                        if not soi:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Item {inv_item['so_item_id']} not found",
                            )

                        remaining = float(soi["quantity"]) - float(
                            soi["quantity_invoiced"]
                        )
                        qty = inv_item.get("quantity", remaining)

                        if qty > remaining:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Quantity {qty} exceeds uninvoiced {remaining}",
                            )

                        items_to_invoice.append({**dict(soi), "invoice_qty": qty})
                else:
                    # Invoice all uninvoiced quantities
                    all_items = await conn.fetch(
                        """
                        SELECT * FROM sales_order_items WHERE sales_order_id = $1 AND quantity > quantity_invoiced
                    """,
                        uuid_module.UUID(order_id),
                    )

                    items_to_invoice = [
                        {
                            **dict(i),
                            "invoice_qty": float(i["quantity"])
                            - float(i["quantity_invoiced"]),
                        }
                        for i in all_items
                    ]

                if not items_to_invoice:
                    raise HTTPException(status_code=400, detail="No items to invoice")

                invoice_number = await conn.fetchval(
                    "SELECT generate_sales_invoice_number($1::text, 'INV')",
                    ctx["tenant_id"],
                )

                invoice_id = uuid_module.uuid4()
                invoice_date = (
                    body.invoice_date if body and body.invoice_date else date.today()
                )
                due_date = body.due_date if body and body.due_date else invoice_date

                # Calculate totals
                subtotal = 0
                tax_total = 0
                for item in items_to_invoice:
                    ratio = Decimal(str(item["invoice_qty"])) / Decimal(
                        str(item["quantity"])
                    )
                    item_subtotal = int(
                        Decimal(str(item["line_total"] - item["tax_amount"])) * ratio
                    )
                    item_tax = int(Decimal(str(item["tax_amount"])) * ratio)
                    subtotal += item_subtotal
                    tax_total += item_tax

                total = subtotal + tax_total

                header_tax_rate = (
                    float(items_to_invoice[0].get("tax_rate") or 0)
                    if items_to_invoice
                    else 0
                )
                # B2 (2026-06-19): pass recognize_at + warehouse_id from request so the
                # caller can reach the canonical PSAK-72 defer path. Null -> existing
                # post-time fallback (tenant_config policy -> 'invoice'), backward-safe.
                _recognize_at = body.recognize_at if body else None
                _warehouse_id = None
                if body and getattr(body, "warehouse_id", None):
                    _warehouse_id = uuid_module.UUID(body.warehouse_id)
                await conn.execute(
                    """
                    INSERT INTO sales_invoices (
                        id, tenant_id, invoice_number, invoice_date, due_date,
                        customer_id, customer_name,
                        subtotal, tax_rate, tax_amount, total_amount,
                        status, sales_order_id, created_by,
                        recognize_at, warehouse_id,
                        payment_bank_name, payment_account_number, payment_account_holder
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12, $13, $14, $15, $16, $17, $18)
                """,
                    invoice_id,
                    ctx["tenant_id"],
                    invoice_number,
                    invoice_date,
                    due_date,
                    str(order["customer_id"]),
                    order["customer_name"],
                    subtotal,
                    header_tax_rate,
                    tax_total,
                    total,
                    uuid_module.UUID(order_id),
                    ctx["user_id"],
                    _recognize_at,
                    _warehouse_id,
                    # Pewarisan rekening tujuan cetak (tiket MASTER); eksplisit menang.
                    _rek_eksplisit(body, "payment_bank_name")
                    or order["payment_bank_name"],
                    _rek_eksplisit(body, "payment_account_number")
                    or order["payment_account_number"],
                    _rek_eksplisit(body, "payment_account_holder")
                    or order["payment_account_holder"],
                )

                for line_idx, item in enumerate(items_to_invoice, start=1):
                    ratio = Decimal(str(item["invoice_qty"])) / Decimal(
                        str(item["quantity"])
                    )
                    item_subtotal = int(
                        Decimal(str(item["line_total"] - item["tax_amount"])) * ratio
                    )
                    item_tax = int(Decimal(str(item["tax_amount"])) * ratio)
                    item_total = item_subtotal + item_tax
                    # Resolve tax_code_id: prefer SO field, fallback to rate lookup
                    _item_tcid = item.get("tax_id")
                    if not _item_tcid and float(item.get("tax_rate") or 0) > 0:
                        _item_tcid = await conn.fetchval(
                            "SELECT id FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND rate=$2 AND is_active=true ORDER BY (name ILIKE '%%Keluaran%%') DESC LIMIT 1",
                            ctx["tenant_id"],
                            item["tax_rate"],
                        )

                    await conn.execute(
                        """
                        INSERT INTO sales_invoice_items (
                            id, invoice_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_code_id, tax_rate, tax_amount, subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                        uuid_module.uuid4(),
                        invoice_id,
                        item["item_id"],
                        item["description"],
                        item["invoice_qty"],
                        item["unit"],
                        item["unit_price"],
                        item["discount_percent"],
                        _item_tcid,
                        item["tax_rate"],
                        item_tax,
                        item_subtotal,
                        item_total,
                        line_idx,
                    )

                    await conn.execute(
                        """
                        UPDATE sales_order_items SET quantity_invoiced = quantity_invoiced + $2
                        WHERE id = $1
                    """,
                        item["id"],
                        item["invoice_qty"],
                    )

                return SalesOrderResponse(
                    success=True,
                    message="Invoice created from sales order",
                    data={
                        "invoice_id": str(invoice_id),
                        "invoice_number": invoice_number,
                        "order_number": order["order_number"],
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting to invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to convert to invoice")
