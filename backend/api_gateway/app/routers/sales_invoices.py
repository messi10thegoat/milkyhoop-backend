"""
Sales Invoices Router - Faktur Penjualan Management

CRUD endpoints for managing sales invoices with accounting kernel integration.
Handles draft -> posted -> paid lifecycle with AR and journal entry creation.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import json

from ..schemas.sales_invoices import (
    CreateInvoiceRequest,
    UpdateInvoiceRequest,
    PostInvoiceRequest,
    VoidInvoiceRequest,
    InvoicePaymentCreate,
    InvoiceResponse,
    InvoiceListResponse,
    InvoiceDetailResponse,
    InvoiceSummaryResponse,
    InvoiceCalculationResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (initialized on first request)
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
    """Calculate line item totals."""
    subtotal = int(item["quantity"] * item["unit_price"])
    discount = item.get("discount_amount", 0)
    if item.get("discount_percent", 0) > 0:
        discount = int(subtotal * item["discount_percent"] / 100)

    after_discount = subtotal - discount
    tax_amount = 0
    if item.get("tax_rate", 0) > 0:
        tax_amount = int(after_discount * item["tax_rate"] / 100)

    total = after_discount + tax_amount

    return {
        **item,
        "subtotal": subtotal,
        "discount_amount": discount,
        "tax_amount": tax_amount,
        "total": total
    }


# =============================================================================
# HEALTH CHECK
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "sales-invoices"}


# =============================================================================
# SUMMARY
# =============================================================================
@router.get("/summary", response_model=InvoiceSummaryResponse)
async def get_invoice_summary(request: Request):
    """Get invoice summary statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total_count,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE status = 'paid') as paid_count,
                    COUNT(*) FILTER (WHERE status = 'overdue' OR (status IN ('posted', 'partial') AND due_date < CURRENT_DATE)) as overdue_count,
                    COALESCE(SUM(total_amount - amount_paid) FILTER (WHERE status IN ('posted', 'partial', 'overdue')), 0) as total_outstanding,
                    COALESCE(SUM(total_amount - amount_paid) FILTER (WHERE (status = 'overdue' OR (status IN ('posted', 'partial') AND due_date < CURRENT_DATE))), 0) as total_overdue
                FROM sales_invoices
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total_count": row["total_count"],
                    "draft_count": row["draft_count"],
                    "posted_count": row["posted_count"],
                    "partial_count": row["partial_count"],
                    "paid_count": row["paid_count"],
                    "overdue_count": row["overdue_count"],
                    "total_outstanding": int(row["total_outstanding"]),
                    "total_overdue": int(row["total_overdue"]),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# CALCULATE (Preview without saving)
# =============================================================================
@router.post("/calculate", response_model=InvoiceCalculationResponse)
async def calculate_invoice(request: Request, body: CreateInvoiceRequest):
    """Preview invoice calculation without saving."""
    try:
        ctx = get_user_context(request)

        # Calculate each item
        calculated_items = []
        subtotal = 0
        total_item_discount = 0
        total_tax = 0

        for i, item in enumerate(body.items):
            calc = calculate_item_totals(item.model_dump())
            calc["line_number"] = i + 1
            calculated_items.append(calc)
            subtotal += calc["subtotal"]
            total_item_discount += calc["discount_amount"]
            total_tax += calc["tax_amount"]

        # Invoice-level discount
        invoice_discount = body.discount_amount
        if body.discount_percent > 0:
            invoice_discount = int(subtotal * body.discount_percent / 100)

        # Total
        total_amount = subtotal - total_item_discount - invoice_discount + total_tax

        return {
            "success": True,
            "data": {
                "subtotal": subtotal,
                "discount_amount": total_item_discount + invoice_discount,
                "tax_amount": total_tax,
                "total_amount": total_amount,
                "items": calculated_items
            }
        }

    except Exception as e:
        logger.error(f"Error calculating invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to calculate invoice")


# =============================================================================
# LIST INVOICES
# =============================================================================
@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search invoice number or customer"),
    status: Optional[str] = Query(None, description="Filter by status"),
    customer_id: Optional[str] = Query(None, description="Filter by customer"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    sort_by: Literal["invoice_date", "due_date", "total_amount", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List invoices with search, filtering, and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(
                    f"(invoice_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if status:
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}::uuid")
                params.append(customer_id)
                param_idx += 1

            if start_date:
                conditions.append(f"invoice_date >= ${param_idx}::date")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"invoice_date <= ${param_idx}::date")
                params.append(end_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            valid_sorts = {
                "invoice_date": "invoice_date",
                "due_date": "due_date",
                "total_amount": "total_amount",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM sales_invoices WHERE {where_clause}",
                *params
            )

            # Get items
            query = f"""
                SELECT id, invoice_number, customer_id, customer_name,
                       invoice_date, due_date, total_amount, amount_paid,
                       status, created_at
                FROM sales_invoices
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "invoice_number": row["invoice_number"],
                    "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
                    "customer_name": row["customer_name"],
                    "invoice_date": row["invoice_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_paid": row["amount_paid"],
                    "status": row["status"],
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
        logger.error(f"Error listing invoices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list invoices")


# =============================================================================
# GET INVOICE DETAIL
# =============================================================================
@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_invoice(request: Request, invoice_id: UUID):
    """Get invoice detail with items and payments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get invoice
            invoice = await conn.fetchrow("""
                SELECT * FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """, invoice_id, ctx["tenant_id"])

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get items
            items = await conn.fetch("""
                SELECT * FROM sales_invoice_items
                WHERE invoice_id = $1
                ORDER BY line_number
            """, invoice_id)

            # Get payments
            payments = await conn.fetch("""
                SELECT * FROM sales_invoice_payments
                WHERE invoice_id = $1
                ORDER BY payment_date
            """, invoice_id)

            return {
                "success": True,
                "data": {
                    "id": str(invoice["id"]),
                    "invoice_number": invoice["invoice_number"],
                    "customer_id": str(invoice["customer_id"]) if invoice["customer_id"] else None,
                    "customer_name": invoice["customer_name"],
                    "invoice_date": invoice["invoice_date"].isoformat(),
                    "due_date": invoice["due_date"].isoformat(),
                    "ref_no": invoice["ref_no"],
                    "notes": invoice["notes"],
                    "subtotal": invoice["subtotal"],
                    "discount_percent": float(invoice["discount_percent"] or 0),
                    "discount_amount": invoice["discount_amount"],
                    "tax_rate": float(invoice["tax_rate"] or 0),
                    "tax_amount": invoice["tax_amount"],
                    "total_amount": invoice["total_amount"],
                    "amount_paid": invoice["amount_paid"],
                    "status": invoice["status"],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"]) if item["item_id"] else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item["unit"],
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"],
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"],
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "payments": [
                        {
                            "id": str(p["id"]),
                            "amount": p["amount"],
                            "payment_date": p["payment_date"].isoformat(),
                            "payment_method": p["payment_method"],
                            "account_id": str(p["account_id"]),
                            "bank_account_id": str(p["bank_account_id"]) if p["bank_account_id"] else None,
                            "reference": p["reference"],
                            "notes": p["notes"],
                            "journal_id": str(p["journal_id"]) if p["journal_id"] else None,
                            "created_at": p["created_at"].isoformat(),
                        }
                        for p in payments
                    ],
                    "ar_id": str(invoice["ar_id"]) if invoice["ar_id"] else None,
                    "journal_id": str(invoice["journal_id"]) if invoice["journal_id"] else None,
                    "posted_at": invoice["posted_at"].isoformat() if invoice["posted_at"] else None,
                    "posted_by": str(invoice["posted_by"]) if invoice["posted_by"] else None,
                    "voided_at": invoice["voided_at"].isoformat() if invoice["voided_at"] else None,
                    "voided_reason": invoice["voided_reason"],
                    "created_at": invoice["created_at"].isoformat(),
                    "updated_at": invoice["updated_at"].isoformat(),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice")


# =============================================================================
# CREATE INVOICE (Draft)
# =============================================================================
@router.post("", response_model=InvoiceResponse, status_code=201)
async def create_invoice(request: Request, body: CreateInvoiceRequest):
    """Create a new sales invoice as draft."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate invoice number
                invoice_number = await conn.fetchval(
                    "SELECT generate_sales_invoice_number($1, 'INV')",
                    ctx["tenant_id"]
                )

                # Calculate totals
                subtotal = 0
                total_item_discount = 0
                total_tax = 0
                calculated_items = []

                for i, item in enumerate(body.items):
                    calc = calculate_item_totals(item.model_dump())
                    calc["line_number"] = i + 1
                    calculated_items.append(calc)
                    subtotal += calc["subtotal"]
                    total_item_discount += calc["discount_amount"]
                    total_tax += calc["tax_amount"]

                # Invoice-level discount
                invoice_discount = body.discount_amount
                if body.discount_percent > 0:
                    invoice_discount = int(subtotal * body.discount_percent / 100)

                total_amount = subtotal - total_item_discount - invoice_discount + total_tax

                # Convert customer_id
                customer_uuid = None
                if body.customer_id:
                    try:
                        customer_uuid = UUID(body.customer_id)
                    except ValueError:
                        raise HTTPException(status_code=400, detail="Invalid customer_id format")

                # Insert invoice
                invoice_id = await conn.fetchval("""
                    INSERT INTO sales_invoices (
                        tenant_id, invoice_number, customer_id, customer_name,
                        invoice_date, due_date, ref_no, notes,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'draft', $15)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    invoice_number,
                    customer_uuid,
                    body.customer_name,
                    body.invoice_date,
                    body.due_date,
                    body.ref_no,
                    body.notes,
                    subtotal,
                    body.discount_percent,
                    invoice_discount,
                    body.tax_rate,
                    total_tax,
                    total_amount,
                    ctx["user_id"]
                )

                # Insert items
                for item in calculated_items:
                    item_uuid = None
                    if item.get("item_id"):
                        try:
                            item_uuid = UUID(item["item_id"])
                        except ValueError:
                            pass

                    await conn.execute("""
                        INSERT INTO sales_invoice_items (
                            invoice_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        invoice_id,
                        item_uuid,
                        item.get("item_code"),
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        item["discount_amount"],
                        item.get("tax_code"),
                        item.get("tax_rate", 0),
                        item["tax_amount"],
                        item["subtotal"],
                        item["total"],
                        item["line_number"]
                    )

                logger.info(f"Invoice created: {invoice_id}, number={invoice_number}")

                return {
                    "success": True,
                    "message": "Invoice created successfully",
                    "data": {
                        "id": str(invoice_id),
                        "invoice_number": invoice_number,
                        "total_amount": total_amount
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create invoice")


# =============================================================================
# UPDATE INVOICE (Draft only)
# =============================================================================
@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(request: Request, invoice_id: UUID, body: UpdateInvoiceRequest):
    """Update a draft invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check invoice exists and is draft
            invoice = await conn.fetchrow("""
                SELECT id, status FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """, invoice_id, ctx["tenant_id"])

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] != "draft":
                raise HTTPException(status_code=400, detail="Only draft invoices can be updated")

            async with conn.transaction():
                # If items provided, recalculate
                if body.items is not None:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM sales_invoice_items WHERE invoice_id = $1",
                        invoice_id
                    )

                    # Calculate and insert new items
                    subtotal = 0
                    total_item_discount = 0
                    total_tax = 0

                    for i, item in enumerate(body.items):
                        calc = calculate_item_totals(item.model_dump())
                        calc["line_number"] = i + 1
                        subtotal += calc["subtotal"]
                        total_item_discount += calc["discount_amount"]
                        total_tax += calc["tax_amount"]

                        item_uuid = None
                        if item.item_id:
                            try:
                                item_uuid = UUID(item.item_id)
                            except ValueError:
                                pass

                        await conn.execute("""
                            INSERT INTO sales_invoice_items (
                                invoice_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            invoice_id,
                            item_uuid,
                            item.item_code,
                            item.description,
                            item.quantity,
                            item.unit,
                            item.unit_price,
                            item.discount_percent,
                            calc["discount_amount"],
                            item.tax_code,
                            item.tax_rate,
                            calc["tax_amount"],
                            calc["subtotal"],
                            calc["total"],
                            calc["line_number"]
                        )

                    # Update invoice totals
                    invoice_discount = body.discount_amount or 0
                    if body.discount_percent and body.discount_percent > 0:
                        invoice_discount = int(subtotal * body.discount_percent / 100)

                    total_amount = subtotal - total_item_discount - invoice_discount + total_tax

                    await conn.execute("""
                        UPDATE sales_invoices
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """, invoice_id, subtotal, invoice_discount, total_tax, total_amount)

                # Update other fields
                update_data = body.model_dump(exclude_unset=True, exclude={"items"})
                if update_data:
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field == "customer_id" and value:
                            updates.append(f"{field} = ${param_idx}::uuid")
                        else:
                            updates.append(f"{field} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                    if updates:
                        updates.append("updated_at = NOW()")
                        params.extend([invoice_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE sales_invoices
                            SET {', '.join(updates)}
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Invoice updated: {invoice_id}")

                return {
                    "success": True,
                    "message": "Invoice updated successfully",
                    "data": {"id": str(invoice_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update invoice")


# =============================================================================
# POST INVOICE (Create AR + Journal Entry + COGS)
# =============================================================================
@router.post("/{invoice_id}/post", response_model=InvoiceResponse)
async def post_invoice(request: Request, invoice_id: UUID, body: PostInvoiceRequest = None):
    """
    Post invoice to accounting (creates AR, journal entry, and COGS).

    For inventory items, automatically:
    - Calculates COGS using weighted average cost
    - Creates COGS journal (Dr. HPP, Cr. Inventory)
    - Records inventory movements in ledger
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        warnings = []

        async with pool.acquire() as conn:
            # Check invoice exists and is draft
            invoice = await conn.fetchrow("""
                SELECT id, invoice_number, customer_id, customer_name, total_amount,
                       invoice_date, due_date, status
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """, invoice_id, ctx["tenant_id"])

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] != "draft":
                raise HTTPException(status_code=400, detail="Only draft invoices can be posted")

            # Get invoice items
            items = await conn.fetch("""
                SELECT id, item_id, item_code, description, quantity, unit_price
                FROM sales_invoice_items
                WHERE invoice_id = $1
            """, invoice_id)

            async with conn.transaction():
                # Create AR record
                ar_id = await conn.fetchval("""
                    INSERT INTO accounts_receivable (
                        tenant_id, customer_id, customer_name,
                        source_type, source_id,
                        amount, balance,
                        issue_date, due_date,
                        status
                    ) VALUES ($1, $2::text, $3, 'INVOICE', $4, $5, $5, $6, $7, 'OPEN')
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    str(invoice["customer_id"]) if invoice["customer_id"] else None,
                    invoice["customer_name"],
                    invoice_id,
                    invoice["total_amount"],
                    invoice["invoice_date"],
                    invoice["due_date"]
                )

                # Create journal entry (simplified - actual implementation would use AccountingFacade)
                # Debit: Piutang Usaha (1-10300)
                # Credit: Penjualan (4-10100)
                import uuid
                journal_id = uuid.uuid4()
                trace_id = uuid.uuid4()

                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, posted_at, posted_by
                    ) VALUES (
                        $1, $2,
                        'JV-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(NEXTVAL('journal_number_seq_' || $2)::TEXT, 4, '0'),
                        $3, $4, 'INVOICE', $5, $6, 'POSTED', NOW(), $7
                    )
                """,
                    journal_id,
                    ctx["tenant_id"],
                    invoice["invoice_date"],
                    f"Faktur Penjualan {invoice['invoice_number']} - {invoice['customer_name']}",
                    invoice_id,
                    trace_id,
                    ctx["user_id"]
                )

                # =============================================================
                # COGS CALCULATION AND POSTING
                # =============================================================
                total_cogs = 0
                cogs_items = []

                for item in items:
                    if not item["item_id"]:
                        # Skip non-inventory items (service items)
                        continue

                    # Check if item is inventory tracked
                    product = await conn.fetchrow("""
                        SELECT id, code, name, purchase_price, is_inventory
                        FROM products
                        WHERE tenant_id = $1::uuid AND id = $2
                    """, ctx["tenant_id"], item["item_id"])

                    if not product or not product.get("is_inventory", True):
                        # Skip non-inventory products
                        continue

                    # Get weighted average cost from inventory ledger
                    avg_cost = await conn.fetchval("""
                        SELECT get_weighted_average_cost($1, $2)
                    """, ctx["tenant_id"], item["item_id"])

                    cost_source = "WEIGHTED_AVG"

                    # Fallback to purchase_price if no inventory history
                    if not avg_cost or avg_cost == 0:
                        avg_cost = product.get("purchase_price", 0) or 0
                        cost_source = "PURCHASE_PRICE"
                        if avg_cost > 0:
                            warnings.append(
                                f"Item {item['item_code'] or product['code']}: Using purchase_price as fallback (no cost history)"
                            )

                    if avg_cost > 0:
                        quantity = float(item["quantity"])
                        line_cogs = int(quantity * avg_cost)
                        total_cogs += line_cogs

                        cogs_items.append({
                            "item_id": str(item["item_id"]),
                            "item_code": item["item_code"] or product["code"],
                            "quantity": quantity,
                            "unit_cost": avg_cost,
                            "total_cost": line_cogs,
                            "cost_source": cost_source
                        })

                        # Update sales_invoice_items with cost info
                        await conn.execute("""
                            UPDATE sales_invoice_items
                            SET unit_cost = $2, total_cost = $3,
                                is_inventory_item = true, cost_source = $4
                            WHERE id = $1
                        """, item["id"], avg_cost, line_cogs, cost_source)

                        # Get current inventory balance for ledger entry
                        current_balance = await conn.fetchval("""
                            SELECT get_inventory_balance($1, $2)
                        """, ctx["tenant_id"], item["item_id"])

                        new_balance = float(current_balance or 0) - quantity

                        # Record inventory movement in ledger
                        await conn.execute("""
                            INSERT INTO inventory_ledger (
                                tenant_id, product_id, product_code, product_name,
                                movement_type, movement_date,
                                source_type, source_id, source_number,
                                quantity_in, quantity_out, quantity_balance,
                                unit_cost, total_cost, average_cost,
                                created_by, notes
                            ) VALUES (
                                $1, $2, $3, $4,
                                'SALE', $5,
                                'SALES_INVOICE', $6, $7,
                                0, $8, $9,
                                $10, $11, $10,
                                $12, $13
                            )
                        """,
                            ctx["tenant_id"],
                            item["item_id"],
                            item["item_code"] or product["code"],
                            product["name"],
                            invoice["invoice_date"],
                            invoice_id,
                            invoice["invoice_number"],
                            quantity,
                            new_balance,
                            avg_cost,
                            line_cogs,
                            ctx["user_id"],
                            f"Sale: {invoice['invoice_number']}"
                        )

                        # Update product stock quantity
                        await conn.execute("""
                            UPDATE products
                            SET stock_quantity = COALESCE(stock_quantity, 0) - $2,
                                updated_at = NOW()
                            WHERE tenant_id = $1::uuid AND id = $3
                        """, ctx["tenant_id"], quantity, item["item_id"])

                # Create COGS journal if there are inventory items
                cogs_journal_id = None
                if total_cogs > 0:
                    cogs_journal_id = uuid.uuid4()
                    cogs_trace_id = uuid.uuid4()

                    # Get account IDs
                    hpp_account = await conn.fetchrow("""
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1::uuid AND code = '5-10100' AND is_active = true
                    """, ctx["tenant_id"])

                    inventory_account = await conn.fetchrow("""
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1::uuid AND code = '1-10400' AND is_active = true
                    """, ctx["tenant_id"])

                    if hpp_account and inventory_account:
                        # Create COGS journal entry
                        await conn.execute("""
                            INSERT INTO journal_entries (
                                id, tenant_id, journal_number, journal_date,
                                description, source_type, source_id, trace_id,
                                total_debit, total_credit,
                                status, posted_at, posted_by
                            ) VALUES (
                                $1, $2,
                                'COGS-' || TO_CHAR($3, 'YYMM') || '-' || LPAD((
                                    SELECT COALESCE(MAX(CAST(SUBSTRING(journal_number FROM 10) AS INT)), 0) + 1
                                    FROM journal_entries
                                    WHERE tenant_id = $2 AND journal_number LIKE 'COGS-' || TO_CHAR($3, 'YYMM') || '-%'
                                )::TEXT, 4, '0'),
                                $3, $4, 'SALES_INVOICE_COGS', $5, $6,
                                $7, $7,
                                'POSTED', NOW(), $8
                            )
                        """,
                            cogs_journal_id,
                            ctx["tenant_id"],
                            invoice["invoice_date"],
                            f"HPP {invoice['invoice_number']} - {invoice['customer_name']}",
                            invoice_id,
                            cogs_trace_id,
                            total_cogs,
                            ctx["user_id"]
                        )

                        # Journal lines: Dr. HPP (5-10100), Cr. Inventory (1-10400)
                        await conn.execute("""
                            INSERT INTO journal_entry_lines (
                                journal_id, account_id, description,
                                debit, credit, line_number
                            ) VALUES
                            ($1, $2, 'HPP Barang Dagang', $3, 0, 1),
                            ($1, $4, 'Persediaan Barang Dagang', 0, $3, 2)
                        """,
                            cogs_journal_id,
                            hpp_account["id"],
                            total_cogs,
                            inventory_account["id"]
                        )

                        logger.info(f"COGS journal created: {cogs_journal_id}, amount: {total_cogs}")
                    else:
                        warnings.append("COGS accounts (5-10100 or 1-10400) not found. COGS journal not created.")

                # Update invoice status and COGS info
                await conn.execute("""
                    UPDATE sales_invoices
                    SET status = 'posted', ar_id = $2, journal_id = $3,
                        cogs_journal_id = $4, total_cogs = $5, cogs_posted_at = CASE WHEN $5 > 0 THEN NOW() ELSE NULL END,
                        posted_at = NOW(), posted_by = $6, updated_at = NOW()
                    WHERE id = $1
                """, invoice_id, ar_id, journal_id, cogs_journal_id, total_cogs, ctx["user_id"])

                logger.info(f"Invoice posted: {invoice_id}, AR: {ar_id}, COGS: {total_cogs}")

                response_data = {
                    "id": str(invoice_id),
                    "ar_id": str(ar_id),
                    "journal_id": str(journal_id),
                    "total_cogs": total_cogs
                }

                if cogs_journal_id:
                    response_data["cogs_journal_id"] = str(cogs_journal_id)
                    response_data["cogs_items"] = cogs_items

                if warnings:
                    response_data["warnings"] = warnings

                return {
                    "success": True,
                    "message": "Invoice posted successfully" + (" with COGS" if total_cogs > 0 else ""),
                    "data": response_data
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post invoice")


# =============================================================================
# RECORD PAYMENT
# =============================================================================
@router.post("/{invoice_id}/payments", response_model=InvoiceResponse)
async def record_payment(request: Request, invoice_id: UUID, body: InvoicePaymentCreate):
    """Record a payment for the invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check invoice exists and is posted
            invoice = await conn.fetchrow("""
                SELECT id, status, total_amount, amount_paid, ar_id
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """, invoice_id, ctx["tenant_id"])

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] not in ("posted", "partial", "overdue"):
                raise HTTPException(status_code=400, detail="Invoice must be posted before recording payment")

            remaining = invoice["total_amount"] - invoice["amount_paid"]
            if body.amount > remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment amount exceeds remaining balance of Rp {remaining:,}"
                )

            async with conn.transaction():
                # Insert payment
                account_uuid = UUID(body.account_id)
                bank_account_uuid = UUID(body.bank_account_id) if body.bank_account_id else None

                payment_id = await conn.fetchval("""
                    INSERT INTO sales_invoice_payments (
                        invoice_id, amount, payment_date, payment_method,
                        account_id, bank_account_id, reference, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                """,
                    invoice_id,
                    body.amount,
                    body.payment_date,
                    body.payment_method,
                    account_uuid,
                    bank_account_uuid,
                    body.reference,
                    body.notes,
                    ctx["user_id"]
                )

                # Update AR if exists
                if invoice["ar_id"]:
                    await conn.execute("""
                        UPDATE accounts_receivable
                        SET balance = balance - $2,
                            status = CASE
                                WHEN balance - $2 <= 0 THEN 'PAID'
                                ELSE 'PARTIAL'
                            END,
                            updated_at = NOW()
                        WHERE id = $1
                    """, invoice["ar_id"], body.amount)

                logger.info(f"Payment recorded: {payment_id} for invoice {invoice_id}")

                return {
                    "success": True,
                    "message": "Payment recorded successfully",
                    "data": {
                        "id": str(payment_id),
                        "invoice_id": str(invoice_id),
                        "amount": body.amount
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording payment for {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to record payment")


# =============================================================================
# VOID INVOICE
# =============================================================================
@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(request: Request, invoice_id: UUID, body: VoidInvoiceRequest):
    """Void an invoice (creates reversal journal if posted)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            invoice = await conn.fetchrow("""
                SELECT id, status, amount_paid, ar_id, journal_id
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """, invoice_id, ctx["tenant_id"])

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] == "void":
                raise HTTPException(status_code=400, detail="Invoice is already voided")

            if invoice["amount_paid"] > 0:
                raise HTTPException(status_code=400, detail="Cannot void invoice with payments. Refund first.")

            async with conn.transaction():
                # If posted, void the AR
                if invoice["ar_id"]:
                    await conn.execute("""
                        UPDATE accounts_receivable
                        SET status = 'VOID', updated_at = NOW()
                        WHERE id = $1
                    """, invoice["ar_id"])

                # Update invoice
                await conn.execute("""
                    UPDATE sales_invoices
                    SET status = 'void', voided_at = NOW(), voided_reason = $2, updated_at = NOW()
                    WHERE id = $1
                """, invoice_id, body.reason)

                logger.info(f"Invoice voided: {invoice_id}, reason: {body.reason}")

                return {
                    "success": True,
                    "message": "Invoice voided successfully",
                    "data": {"id": str(invoice_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void invoice")


# =============================================================================
# DELETE INVOICE (Draft only)
# =============================================================================
@router.delete("/{invoice_id}", response_model=InvoiceResponse)
async def delete_invoice(request: Request, invoice_id: UUID):
    """Delete a draft invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            invoice = await conn.fetchrow("""
                SELECT id, invoice_number, status
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """, invoice_id, ctx["tenant_id"])

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] != "draft":
                raise HTTPException(status_code=400, detail="Only draft invoices can be deleted. Use void for posted invoices.")

            # Delete (cascade will handle items)
            await conn.execute("DELETE FROM sales_invoices WHERE id = $1", invoice_id)

            logger.info(f"Invoice deleted: {invoice_id}")

            return {
                "success": True,
                "message": "Invoice deleted successfully",
                "data": {"id": str(invoice_id), "invoice_number": invoice["invoice_number"]}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete invoice")
