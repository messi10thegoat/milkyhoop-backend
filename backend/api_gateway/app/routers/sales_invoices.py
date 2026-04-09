"""
Sales Invoices Router - Faktur Penjualan Management

CRUD endpoints for managing sales invoices with accounting kernel integration.
Handles draft -> posted -> paid lifecycle with AR and journal entry creation.
"""

from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.sales_invoices import (
    CreateInvoiceRequest,
    UpdateInvoiceRequest,
    PostInvoiceRequest,
    VoidInvoiceRequest,
    InvoicePaymentCreate,
    InvoiceResponse,
    InvoiceListResponse,
    InvoiceSummaryResponse,
    InvoiceCalculationResponse,
)
from ..config import settings
from ..services.resolve_account import resolve_account_id
from ..utils.idempotency import get_idempotency_key

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
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    try:
        user_uuid = UUID(user_id) if user_id else None
    except (ValueError, TypeError):
        user_uuid = None
    return {"tenant_id": tenant_id, "user_id": user_uuid}


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    """Check if the accounting period for the transaction date is open."""
    period = await conn.fetchrow(
        """
        SELECT id, period_name, status FROM fiscal_periods
        WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
        ORDER BY start_date DESC LIMIT 1
        """,
        tenant_id,
        transaction_date,
    )

    if period and period["status"] in ("CLOSED", "LOCKED"):
        period_name = period["period_name"]
        period_status = period["status"].lower()
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period_status} period ({period_name})",
        )


def _d(v) -> Decimal:
    """Convert any numeric to Decimal for PSAK/IFRS precision."""
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal("0")


def _r2(v: Decimal) -> float:
    """Round Decimal to 2 decimal places, return float for JSON/DB."""
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_item_totals(item: dict) -> dict:
    """Calculate line item totals with Decimal precision (PSAK/IFRS)."""
    qty = _d(item["quantity"])
    price = _d(item["unit_price"])
    subtotal = qty * price

    discount = _d(item.get("discount_amount", 0))
    if item.get("discount_percent", 0) > 0:
        discount = subtotal * _d(item["discount_percent"]) / Decimal("100")

    after_discount = subtotal - discount
    tax_amount = Decimal("0")
    if item.get("tax_rate", 0) > 0:
        tax_amount = after_discount * _d(item["tax_rate"]) / Decimal("100")

    total = after_discount + tax_amount

    return {
        **item,
        "subtotal": _r2(subtotal),
        "discount_amount": _r2(discount),
        "tax_amount": _r2(tax_amount),
        "total": _r2(total),
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
            # Pure Ledger: Summary via compute_ar_outstanding() DB function
            query = """
                WITH ar_fn AS (
                    SELECT invoice_id, outstanding
                    FROM compute_ar_outstanding($1)
                )
                SELECT
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1) as total_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'draft') as draft_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'posted') as posted_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'partial') as partial_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'paid') as paid_count,
                    (SELECT COUNT(*) FROM sales_invoices si3
                     LEFT JOIN ar_fn aw ON aw.invoice_id = si3.id
                     WHERE si3.tenant_id = $1
                       AND (si3.status = 'overdue' OR (si3.status IN ('posted', 'partial') AND si3.due_date < CURRENT_DATE))
                       AND aw.outstanding > 0
                    ) as overdue_count,
                    COALESCE((SELECT SUM(outstanding) FROM ar_fn), 0) as total_outstanding,
                    COALESCE((
                        SELECT SUM(aw.outstanding)
                        FROM ar_fn aw
                        JOIN sales_invoices si3 ON si3.id = aw.invoice_id
                        WHERE si3.status = 'overdue'
                           OR (si3.status IN ('posted', 'partial') AND si3.due_date < CURRENT_DATE)
                    ), 0) as total_overdue
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "status": "draft",
                    "total_count": row["total_count"],
                    "draft_count": row["draft_count"],
                    "posted_count": row["posted_count"],
                    "partial_count": row["partial_count"],
                    "paid_count": row["paid_count"],
                    "overdue_count": row["overdue_count"],
                    "total_outstanding": float(row["total_outstanding"]),
                    "total_overdue": float(row["total_overdue"]),
                },
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
            invoice_discount = _r2(
                _d(subtotal) * _d(body.discount_percent) / Decimal("100")
            )

        # Total
        total_amount = subtotal - total_item_discount - invoice_discount + total_tax

        return {
            "success": True,
            "data": {
                "status": "draft",
                "subtotal": subtotal,
                "discount_amount": total_item_discount + invoice_discount,
                "tax_amount": total_tax,
                "total_amount": total_amount,
                "items": calculated_items,
            },
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
    search: Optional[str] = Query(
        None, description="Search invoice number or customer"
    ),
    status: Optional[str] = Query(None, description="Filter by status"),
    customer_id: Optional[str] = Query(None, description="Filter by customer"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    sort_by: Literal["invoice_date", "due_date", "total_amount", "created_at"] = Query(
        "created_at"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    amount_min: Optional[float] = Query(None, description="Minimum amount filter"),
    amount_max: Optional[float] = Query(None, description="Maximum amount filter"),
):
    """List invoices with search, filtering, and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["si.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(si.invoice_number ILIKE ${param_idx} OR si.customer_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(si.invoice_number ILIKE ${param_idx} OR si.customer_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            # Status filter (with dynamic overdue calculation like bills)
            if status:
                if status == "active":
                    # Exclude draft & void — for piutang/AR queries
                    conditions.append("si.status NOT IN ('draft', 'void')")
                elif status == "overdue":
                    # Overdue = posted/partial + past due + has outstanding via DB function
                    conditions.append(
                        "(si.status IN ('posted', 'partial') AND si.due_date < CURRENT_DATE"
                        " AND si.id IN (SELECT invoice_id FROM compute_ar_outstanding($1) WHERE outstanding > 0))"
                    )
                else:
                    conditions.append(f"si.status = ${param_idx}")
                    params.append(status)
                    param_idx += 1

            if customer_id:
                conditions.append(f"si.customer_id = ${param_idx}::uuid")
                params.append(customer_id)
                param_idx += 1

            if start_date:
                conditions.append(f"si.invoice_date >= ${param_idx}::date")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"si.invoice_date <= ${param_idx}::date")
                params.append(end_date)
                param_idx += 1

            # Amount range filter
            if amount_min is not None:
                conditions.append(f"si.total_amount >= ${param_idx}")
                params.append(amount_min)
                param_idx += 1
            if amount_max is not None:
                conditions.append(f"si.total_amount <= ${param_idx}")
                params.append(amount_max)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            valid_sorts = {
                "invoice_date": "si.invoice_date",
                "due_date": "si.due_date",
                "total_amount": "si.total_amount",
                "created_at": "si.created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM sales_invoices si WHERE {where_clause}", *params
            )

            # Pure Ledger: derive amount_paid via compute_ar_outstanding() DB function
            query = f"""
                SELECT si.id, si.invoice_number, si.customer_id, si.customer_name,
                       si.invoice_date, si.due_date, si.total_amount,
                       si.total_amount - COALESCE(ar_fn.outstanding, 0) as journal_paid,
                       si.status, si.operational_status, si.accounting_status, si.created_at
                FROM sales_invoices si
                LEFT JOIN compute_ar_outstanding($1) ar_fn ON ar_fn.invoice_id = si.id
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
                    "customer_id": str(row["customer_id"])
                    if row["customer_id"]
                    else None,
                    "customer_name": row["customer_name"],
                    "invoice_date": row["invoice_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_paid": int(row["journal_paid"])
                    if row["journal_paid"] is not None
                    else 0,
                    "status": row["status"],
                    "operational_status": row.get("operational_status") or "DRAFT",
                    "accounting_status": row.get("accounting_status") or "UNPOSTED",
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing invoices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list invoices")


# =============================================================================
# GET INVOICE DETAIL
# =============================================================================
@router.get("/{invoice_id}")
async def get_invoice(request: Request, invoice_id: UUID):
    """Get invoice detail with items and payments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get invoice
            invoice = await conn.fetchrow(
                """
                SELECT * FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM sales_invoice_items
                WHERE invoice_id = $1
                ORDER BY line_number
            """,
                invoice_id,
            )

            # Get payments from receive_payments (NOT deprecated sales_invoice_payments)
            payments = await conn.fetch(
                """
                SELECT rp.id, rp.payment_number, rp.total_amount AS amount,
                       rp.payment_date, rp.payment_method,
                       rp.bank_account_id, rp.reference_number AS reference,
                       rp.notes, rp.journal_id, rp.created_at, rp.status,
                       ba.account_name AS bank_account_name
                FROM receive_payment_allocations rpa
                JOIN receive_payments rp ON rp.id = rpa.payment_id
                LEFT JOIN bank_accounts ba ON ba.id = rp.bank_account_id
                WHERE rpa.invoice_id = $1
                  AND rp.tenant_id = $2
                  AND rp.status = 'posted'
                ORDER BY rp.payment_date
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            # Pure Ledger: derive amount_paid via compute_ar_outstanding() DB function
            ar_row = await conn.fetchrow(
                """
                SELECT paid_amount, outstanding
                FROM compute_ar_outstanding($1)
                WHERE invoice_id = $2
            """,
                ctx["tenant_id"],
                invoice_id,
            )
            # If no row from function: invoice is fully paid (outstanding=0) or draft/void
            if ar_row:
                journal_amount_paid = float(
                    invoice["total_amount"] - ar_row["outstanding"]
                )
            elif invoice["status"] in ("paid",):
                journal_amount_paid = float(invoice["total_amount"])
            else:
                journal_amount_paid = 0

            return {
                "success": True,
                "data": {
                    "status": invoice["status"],
                    "id": str(invoice["id"]),
                    "invoice_number": invoice["invoice_number"],
                    "customer_id": str(invoice["customer_id"])
                    if invoice["customer_id"]
                    else None,
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
                    "amount_paid": journal_amount_paid,
                    "amount_due": float(ar_row["outstanding"])
                    if ar_row
                    else (
                        0.0
                        if invoice["status"] in ("paid",)
                        else float(invoice["total_amount"] or 0)
                    ),
                    "status": invoice["status"],
                    "operational_status": invoice.get("operational_status") or "DRAFT",
                    "accounting_status": invoice.get("accounting_status") or "UNPOSTED",
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item.get("unit"),
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"],
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"],
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                            "batch_id": str(item["batch_id"])
                            if item.get("batch_id")
                            else None,
                            "batch_no": item.get("batch_no"),
                            "exp_date": item["exp_date"].isoformat()
                            if item.get("exp_date")
                            else None,
                        }
                        for item in items
                    ],
                    "lines": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item.get("unit"),
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"],
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"],
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                            "batch_id": str(item["batch_id"])
                            if item.get("batch_id")
                            else None,
                            "batch_no": item.get("batch_no"),
                            "exp_date": item["exp_date"].isoformat()
                            if item.get("exp_date")
                            else None,
                        }
                        for item in items
                    ],
                    "payments": [
                        {
                            "id": str(p["id"]),
                            "payment_number": p.get("payment_number") or "-",
                            "amount": float(p["amount"] or 0),
                            "payment_date": p["payment_date"].isoformat()
                            if p["payment_date"]
                            else None,
                            "payment_method": p.get("payment_method"),
                            "bank_account_id": str(p["bank_account_id"])
                            if p.get("bank_account_id")
                            else None,
                            "bank_account_name": p.get("bank_account_name"),
                            "reference": p.get("reference"),
                            "notes": p.get("notes"),
                            "journal_id": str(p["journal_id"])
                            if p.get("journal_id")
                            else None,
                            "created_at": p["created_at"].isoformat()
                            if p.get("created_at")
                            else None,
                            "status": p.get("status"),
                        }
                        for p in payments
                    ],
                    "ar_id": str(invoice["ar_id"]) if invoice["ar_id"] else None,
                    "journal_id": str(invoice["journal_id"])
                    if invoice["journal_id"]
                    else None,
                    "posted_at": invoice["posted_at"].isoformat()
                    if invoice["posted_at"]
                    else None,
                    "posted_by": str(invoice["posted_by"])
                    if invoice["posted_by"]
                    else None,
                    "voided_at": invoice["voided_at"].isoformat()
                    if invoice["voided_at"]
                    else None,
                    "voided_reason": invoice["voided_reason"],
                    "created_at": invoice["created_at"].isoformat(),
                    "updated_at": invoice["updated_at"].isoformat(),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice")


# =============================================================================
# CREATE INVOICE (Draft)
# =============================================================================


# =============================================================================
# INTERNAL HELPER: Post invoice (for auto_post)
# =============================================================================
async def _internal_post_invoice(conn, ctx, invoice_id, invoice_number, total_amount):
    """Internal helper to post an invoice within the same transaction."""
    import uuid
    from datetime import date as dt_date

    # Law 13: Advisory lock - prevent concurrent posting
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))", f"INVOICE:{invoice_id}"
    )

    # Get invoice data
    invoice = await conn.fetchrow(
        """
        SELECT id, invoice_number, customer_id, customer_name, total_amount,
               tax_amount, subtotal, invoice_date, due_date, warehouse_id
        FROM sales_invoices WHERE id = $1 AND tenant_id = $2
        """,
        invoice_id,
        ctx["tenant_id"],
    )

    # Create AR record
    ar_id = await conn.fetchval(
        """
        INSERT INTO accounts_receivable (
            tenant_id, customer_id, customer_name,
            source_type, source_id, invoice_number,
            amount, amount_paid,
            invoice_date, due_date, status
        ) VALUES ($1, $2::uuid, $3, 'INVOICE', $4, $5, $6, 0, $7, $8, 'OPEN')
        RETURNING id
        """,
        ctx["tenant_id"],
        invoice["customer_id"],
        invoice["customer_name"],
        invoice_id,
        invoice_number,
        total_amount,
        invoice["invoice_date"],
        invoice["due_date"],
    )

    # Create journal entry
    journal_id = uuid.uuid4()
    trace_id = str(uuid.uuid4())
    today = dt_date.today()
    year_month_str = today.strftime("%y%m")

    journal_seq = await conn.fetchval(
        """
        INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
        VALUES ($1, 'JV', $2, $3, 1)
        ON CONFLICT (tenant_id, prefix, year, month)
        DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number
        """,
        ctx["tenant_id"],
        today.year,
        today.month,
    )
    journal_number = f"JV-{year_month_str}-{journal_seq:04d}"

    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            total_debit, total_credit, status, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'INVOICE', $6, $7, $8, $8, 'DRAFT', $9)
        """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        invoice["invoice_date"],
        f"Faktur Penjualan {invoice_number} - {invoice['customer_name']}",
        invoice_id,
        trace_id,
        total_amount,
        ctx["user_id"],
    )

    # Get AR and Sales accounts
    ar_account = {"id": await resolve_account_id(conn, ctx["tenant_id"], "1-10400")}
    sales_account = {"id": await resolve_account_id(conn, ctx["tenant_id"], "4-10100")}
    vat_output_account = {
        "id": await resolve_account_id(conn, ctx["tenant_id"], "2-10600")
    }

    # Compute subtotal (revenue without tax)
    tax_amount = float(invoice["tax_amount"] or 0)
    subtotal_amount = total_amount - tax_amount
    line_number = 1

    # Insert journal lines
    # Line 1: Debit AR = total_amount (inclusive of tax)
    if ar_account:
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            uuid.uuid4(),
            journal_id,
            line_number,
            ar_account["id"],
            total_amount,
            f"Piutang - {invoice_number}",
        )
        line_number += 1

    # Line 2: Credit Sales Revenue = subtotal (WITHOUT tax)
    if sales_account:
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            uuid.uuid4(),
            journal_id,
            line_number,
            sales_account["id"],
            subtotal_amount,
            f"Penjualan - {invoice_number}",
        )
        line_number += 1

    # Line 3: Credit VAT Output = tax_amount (PPN Keluaran)
    if tax_amount > 0 and vat_output_account:
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            uuid.uuid4(),
            journal_id,
            line_number,
            vat_output_account["id"],
            tax_amount,
            f"PPN Keluaran - {invoice_number}",
        )
        line_number += 1

    # =============================================================
    # Law 20: DRAFT->POSTED triggers hash chain
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
    )

    # T3: Write document_tax_lines per taxable item
    tax_items_dtl = await conn.fetch(
        """
        SELECT id, tax_code_id, tax_rate, tax_amount, subtotal, discount_amount, dpp
        FROM sales_invoice_items
        WHERE invoice_id = $1 AND COALESCE(tax_amount, 0) > 0
        """,
        invoice_id,
    )
    # Get the PPN Keluaran journal_line_id
    vat_jl_id = None
    if tax_amount > 0 and vat_output_account.get("id"):
        vat_jl_id = await conn.fetchval(
            """
            SELECT id FROM journal_lines
            WHERE journal_id = $1 AND account_id = $2
            LIMIT 1
            """,
            journal_id,
            vat_output_account["id"],
        )
    for ti in tax_items_dtl:
        _tcid = ti["tax_code_id"]
        if not _tcid:
            if float(ti["tax_rate"] or 0) <= 0 or float(ti["tax_amount"] or 0) <= 0:
                continue
            _tcid = await conn.fetchval(
                "SELECT id FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND rate=$2 AND is_active=true ORDER BY (name ILIKE '%%Keluaran%%') DESC LIMIT 1",
                ctx["tenant_id"], ti["tax_rate"],
            )
            if not _tcid:
                continue
        tc_coa = await conn.fetchval(
            "SELECT coa_id FROM tax_codes WHERE id = $1",
            _tcid,
        )
        dpp_val = float(ti["dpp"] or 0) or (
            float(ti["subtotal"] or 0) - float(ti["discount_amount"] or 0)
        )
        await conn.execute(
            """
            INSERT INTO document_tax_lines (
                id, tenant_id, document_type, document_id, line_item_id,
                tax_code_id, direction, base_amount, tax_amount,
                coa_id, journal_line_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            uuid.uuid4(),
            ctx["tenant_id"],
            "SALES_INVOICE",
            invoice_id,
            ti["id"],
            _tcid,
            "output",
            dpp_val,
            float(ti["tax_amount"]),
            tc_coa,
            vat_jl_id,
        )

    # COGS CALCULATION AND INVENTORY LEDGER (matching post_invoice logic)
    # =============================================================
    # Get invoice items
    items = await conn.fetch(
        """
        SELECT id, item_id, item_code, description, quantity, unit_price, batch_id, batch_no, exp_date
        FROM sales_invoice_items
        WHERE invoice_id = $1
        """,
        invoice_id,
    )

    total_cogs = 0
    cogs_items = []
    warnings = []

    for item in items:
        if not item["item_id"]:
            # Skip non-inventory items (service items)
            continue

        # Check if item is inventory tracked
        product = await conn.fetchrow(
            """
            SELECT id, item_code, nama_produk, purchase_price_amount, track_inventory, track_batches
            FROM products
            WHERE tenant_id = $1 AND id = $2
            """,
            ctx["tenant_id"],
            item["item_id"],
        )

        if not product or not product.get("track_inventory", True):
            # Skip non-inventory products
            continue

        # Get weighted average cost from inventory ledger
        avg_cost = await conn.fetchval(
            """
            SELECT get_weighted_average_cost($1, $2)
            """,
            ctx["tenant_id"],
            item["item_id"],
        )

        cost_source = "WEIGHTED_AVG"

        # Fallback to purchase_price if no inventory history
        if not avg_cost or avg_cost == 0:
            avg_cost = product.get("purchase_price_amount", 0) or 0
            cost_source = "PURCHASE_PRICE"

        if (not avg_cost or avg_cost == 0) and product.get("track_inventory", True):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Tidak bisa post faktur: produk '{product['nama_produk']}' tidak punya "
                    f"riwayat stok / biaya perolehan (WAC=0 dan harga beli=0). "
                    f"Catat penerimaan barang (Bill / Penerimaan Produksi / Opening Stock) terlebih dahulu."
                )
            )

        if avg_cost > 0:
            quantity = float(item["quantity"])
            line_cogs = int(quantity * float(avg_cost))
            total_cogs += line_cogs

            cogs_items.append(
                {
                    "item_id": str(item["item_id"]),
                    "item_code": item["item_code"] or product["item_code"],
                    "quantity": quantity,
                    "unit_cost": avg_cost,
                    "total_cost": line_cogs,
                    "cost_source": cost_source,
                }
            )

            # Update sales_invoice_items with cost info
            await conn.execute(
                """
                UPDATE sales_invoice_items
                SET unit_cost = $2, total_cost = $3,
                    is_inventory_item = true, cost_source = $4
                WHERE id = $1
                """,
                item["id"],
                avg_cost,
                line_cogs,
                cost_source,
            )

            # Get current inventory balance for ledger entry
            current_balance = await conn.fetchval(
                """
                SELECT get_inventory_balance($1, $2)
                """,
                ctx["tenant_id"],
                item["item_id"],
            )

            new_balance = float(current_balance or 0) - quantity

            # Resolve warehouse_id for this posting
            posting_warehouse_id = invoice.get("warehouse_id")
            if not posting_warehouse_id:
                # Fallback: get default warehouse for tenant
                posting_warehouse_id = await conn.fetchval(
                    "SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY created_at LIMIT 1",
                    ctx["tenant_id"],
                )

            # Resolve batch_id (explicit or FEFO auto-allocation)
            si_batch_id = item.get("batch_id")
            if (
                not si_batch_id
                and product.get("track_batches")
                and posting_warehouse_id
            ):
                # FEFO auto-allocation
                fefo_batches = await conn.fetch(
                    "SELECT * FROM get_available_batches($1, $2, $3, $4, 'FEFO')",
                    ctx["tenant_id"],
                    item["item_id"],
                    posting_warehouse_id,
                    quantity,
                )
                if fefo_batches:
                    total_allocated = sum(
                        float(r["quantity_to_use"]) for r in fefo_batches
                    )
                    if total_allocated >= quantity:
                        si_batch_id = fefo_batches[0]["batch_id"]
                    else:
                        raise HTTPException(
                            400,
                            f"Stok batch tidak cukup untuk {product.get('nama_produk', item['item_id'])}",
                        )
                else:
                    raise HTTPException(
                        400,
                        f"Stok batch tidak cukup untuk {product.get('nama_produk', item['item_id'])}",
                    )

            # Record inventory movement in ledger
            await conn.execute(
                """
                INSERT INTO inventory_ledger (
                    tenant_id, product_id, product_code, product_name,
                    movement_type, movement_date,
                    source_type, source_id, source_number,
                    quantity_in, quantity_out, quantity_balance,
                    unit_cost, total_cost, average_cost,
                    created_by, notes, warehouse_id, batch_id
                ) VALUES (
                    $1, $2, $3, $4,
                    'SALE', $5,
                    'SALES_INVOICE', $6, $7,
                    0, $8, $9,
                    $10, $11, $10,
                    $12, $13, $14, $15
                )
                """,
                ctx["tenant_id"],
                item["item_id"],
                item["item_code"] or product["item_code"],
                product["nama_produk"],
                invoice["invoice_date"],
                invoice_id,
                invoice_number,
                quantity,
                new_balance,
                avg_cost,
                line_cogs,
                ctx["user_id"],
                f"Sale: {invoice_number}",
                posting_warehouse_id,
                si_batch_id,
            )

            # Batch deduction (if batch tracking)
            if si_batch_id:
                # Row-level lock + validate
                bws_row = await conn.fetchrow(
                    """
                    SELECT available_quantity FROM batch_warehouse_stock
                    WHERE batch_id = $1 AND warehouse_id = $2
                    FOR UPDATE
                """,
                    si_batch_id,
                    posting_warehouse_id,
                )

                if bws_row is None or float(bws_row["available_quantity"]) < quantity:
                    available = float(bws_row["available_quantity"]) if bws_row else 0
                    raise HTTPException(
                        400,
                        f"Stok batch tidak cukup. Tersedia: {available}, diminta: {quantity}",
                    )

                # Deduct batch_warehouse_stock
                await conn.execute(
                    """
                    UPDATE batch_warehouse_stock
                    SET quantity = quantity - $3,
                        last_movement_date = NOW(), updated_at = NOW()
                    WHERE batch_id = $1 AND warehouse_id = $2
                """,
                    si_batch_id,
                    posting_warehouse_id,
                    quantity,
                )

                # item_batches.current_quantity synced by trg_sync_batch_quantity trigger

                # Mark depleted if needed
                await conn.execute(
                    """
                    UPDATE item_batches SET status = 'depleted'
                    WHERE id = $1 AND current_quantity <= 0 AND status = 'active'
                """,
                    si_batch_id,
                )

    # Create COGS journal if there are inventory items
    cogs_journal_id = None
    if total_cogs > 0:
        cogs_journal_id = uuid.uuid4()
        cogs_trace_id = str(uuid.uuid4())

        # Get account IDs
        hpp_account = {
            "id": await resolve_account_id(conn, ctx["tenant_id"], "5-10100")
        }

        inventory_account = {
            "id": await resolve_account_id(conn, ctx["tenant_id"], "1-10600")
        }

        if hpp_account and inventory_account:
            # Get COGS journal number using sequence
            cogs_seq = await conn.fetchval(
                """
                INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
                VALUES ($1, 'COGS', $2, $3, 1)
                ON CONFLICT (tenant_id, prefix, year, month)
                DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
                RETURNING last_number
                """,
                ctx["tenant_id"],
                today.year,
                today.month,
            )
            cogs_journal_number = f"COGS-{year_month_str}-{cogs_seq:04d}"

            # Create COGS journal entry
            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date,
                    description, source_type, source_id, trace_id,
                    total_debit, total_credit,
                    status, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'SALES_INVOICE_COGS', $6, $7, $8, $8, 'DRAFT', $9)
                """,
                cogs_journal_id,
                ctx["tenant_id"],
                cogs_journal_number,
                invoice["invoice_date"],
                f"HPP {invoice_number} - {invoice['customer_name']}",
                invoice_id,
                cogs_trace_id,
                total_cogs,
                ctx["user_id"],
            )

            # Journal lines: Dr. HPP (5-10100), Cr. Inventory (1-10600)
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    journal_id, account_id, memo,
                    debit, credit, line_number
                ) VALUES
                ($1, $2, 'HPP Barang Dagang', $3, 0, 1),
                ($1, $4, 'Persediaan Barang Dagang', 0, $3, 2)
                """,
                cogs_journal_id,
                hpp_account["id"],
                total_cogs,
                inventory_account["id"],
            )

            # Law 20: DRAFT->POSTED triggers hash chain
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                cogs_journal_id,
            )

            import logging

            logging.getLogger(__name__).info(
                f"COGS journal created in auto_post: {cogs_journal_id}, amount: {total_cogs}"
            )

    # Update invoice status (including COGS info)
    await conn.execute(
        """
        UPDATE sales_invoices
        SET status = 'posted', operational_status = 'SENT', accounting_status = 'POSTED',
            ar_id = $1, journal_id = $2, posted_at = NOW(), posted_by = $3,
            cogs_journal_id = $5, total_cogs = $6::bigint,
            cogs_posted_at = CASE WHEN $6::bigint > 0 THEN NOW() ELSE NULL END
        WHERE id = $4
        """,
        ar_id,
        journal_id,
        ctx["user_id"],
        invoice_id,
        cogs_journal_id,
        total_cogs,
    )

    return {
        "journal_id": str(journal_id),
        "ar_id": str(ar_id),
        "journal_number": journal_number,
        "cogs_journal_id": str(cogs_journal_id) if cogs_journal_id else None,
        "total_cogs": total_cogs,
    }


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
                    "SELECT generate_sales_invoice_number($1, 'INV')", ctx["tenant_id"]
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
                    invoice_discount = _r2(
                        _d(subtotal) * _d(body.discount_percent) / Decimal("100")
                    )

                total_amount = (
                    subtotal - total_item_discount - invoice_discount + total_tax
                )

                # Convert customer_id
                # customer_id is TEXT column, use string directly
                customer_id_str = body.customer_id if body.customer_id else None
                # Validate UUID format if provided
                if customer_id_str:
                    try:
                        UUID(customer_id_str)  # Validate format
                    except ValueError:
                        raise HTTPException(
                            status_code=400, detail="Invalid customer_id format"
                        )

                # BUG-02 fix: Resolve customer_id from customer_name when missing
                if not customer_id_str and body.customer_name:
                    # Exact match first (customers.nama is Bahasa Indonesia column)
                    resolved_id = await conn.fetchval(
                        """SELECT id FROM customers
                           WHERE tenant_id = $1 AND nama = $2
                             AND is_active = true AND deleted_at IS NULL
                           LIMIT 1""",
                        ctx["tenant_id"],
                        body.customer_name,
                    )
                    # Fallback: case-insensitive ILIKE match
                    if not resolved_id:
                        resolved_id = await conn.fetchval(
                            """SELECT id FROM customers
                               WHERE tenant_id = $1 AND nama ILIKE $2
                                 AND is_active = true AND deleted_at IS NULL
                               LIMIT 1""",
                            ctx["tenant_id"],
                            body.customer_name,
                        )
                    if resolved_id:
                        customer_id_str = str(resolved_id)

                # Insert invoice
                invoice_id = await conn.fetchval(
                    """
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
                    customer_id_str,
                    body.customer_name,
                    body.invoice_date,
                    body.due_date,
                    body.ref_no,
                    body.notes,
                    subtotal,
                    body.discount_percent,
                    invoice_discount,
                    (body.tax_rate if body.tax_rate and float(body.tax_rate) > 0 else (max((float(it.tax_rate or 0) for it in body.items), default=0))),
                    total_tax,
                    total_amount,
                    ctx["user_id"],
                )

                # Insert items
                for item in calculated_items:
                    item_uuid = None
                    if item.get("item_id"):
                        try:
                            item_uuid = UUID(item["item_id"])
                        except ValueError:
                            pass

                    # Auto-populate batch_no/exp_date from item_batches if only batch_id provided
                    item_batch_no = item.get("batch_no")
                    item_exp_date = item.get("exp_date")
                    # Parse exp_date string (e.g. "2026-04" or "2026-04-01") to date object
                    if isinstance(item_exp_date, str):
                        try:
                            from datetime import date as _date

                            parts = item_exp_date.split("-")
                            if len(parts) == 2:  # "YYYY-MM" format
                                item_exp_date = _date(int(parts[0]), int(parts[1]), 1)
                            elif len(parts) == 3:  # "YYYY-MM-DD" format
                                item_exp_date = _date(
                                    int(parts[0]), int(parts[1]), int(parts[2])
                                )
                            else:
                                item_exp_date = None
                        except (ValueError, TypeError):
                            item_exp_date = None
                    item_batch_id = item.get("batch_id")
                    if item_batch_id and (not item_batch_no or not item_exp_date):
                        batch_info = await conn.fetchrow(
                            "SELECT batch_number, expiry_date FROM item_batches WHERE id = $1",
                            UUID(item_batch_id)
                            if isinstance(item_batch_id, str)
                            else item_batch_id,
                        )
                        if batch_info:
                            if not item_batch_no:
                                item_batch_no = batch_info["batch_number"]
                            if not item_exp_date:
                                item_exp_date = batch_info["expiry_date"]

                    await conn.execute(
                        """
                        INSERT INTO sales_invoice_items (
                            invoice_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number,
                            batch_id, batch_no, exp_date,
                            tax_code_id, dpp
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
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
                        item["line_number"],
                        UUID(item_batch_id) if item_batch_id else None,
                        item_batch_no,
                        item_exp_date,
                        UUID(item.get("tax_code_id"))
                        if item.get("tax_code_id")
                        else None,
                        item.get("dpp")
                        or (item["subtotal"] - item.get("discount_amount", 0)),
                    )

                logger.info(f"Invoice created: {invoice_id}, number={invoice_number}")

                # If auto_post requested, post the invoice immediately
                if body.auto_post:
                    # Import here to avoid circular import
                    from uuid import UUID as UUID_type

                    # Post the invoice (creates AR, journal, COGS)
                    post_result = await _internal_post_invoice(
                        conn,
                        ctx,
                        UUID_type(str(invoice_id)),
                        invoice_number,
                        total_amount,
                    )

                    return {
                        "success": True,
                        "message": "Invoice created and posted successfully",
                        "data": {
                            "status": "posted",
                            "id": str(invoice_id),
                            "invoice_number": invoice_number,
                            "total_amount": total_amount,
                            "journal_id": post_result.get("journal_id"),
                            "ar_id": post_result.get("ar_id"),
                            "cogs_journal_id": post_result.get("cogs_journal_id"),
                            "total_cogs": post_result.get("total_cogs", 0),
                        },
                    }

                return {
                    "success": True,
                    "message": "Invoice created successfully",
                    "data": {
                        "status": "draft",
                        "id": str(invoice_id),
                        "invoice_number": invoice_number,
                        "total_amount": float(total_amount),
                        "amount_paid": 0,
                        "customer_id": body.customer_id,
                        "customer_name": body.customer_name,
                        "invoice_date": str(body.invoice_date),
                        "due_date": str(body.due_date),
                        "created_at": None,
                    },
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
async def update_invoice(
    request: Request, invoice_id: UUID, body: UpdateInvoiceRequest
):
    """Update a draft invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check invoice exists and is draft
            invoice = await conn.fetchrow(
                """
                SELECT id, status FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Guard: Cannot update voided invoices
            if invoice["status"] == "void":
                raise HTTPException(
                    status_code=400, detail="Cannot update voided invoice"
                )

            # Guard: Cannot update non-draft invoices
            if invoice["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot edit posted invoice. Only draft invoices can be edited.",
                )

            async with conn.transaction():
                # If items provided, recalculate
                if body.items is not None:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM sales_invoice_items WHERE invoice_id = $1",
                        invoice_id,
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

                        # Auto-populate batch_no/exp_date from item_batches if only batch_id provided
                        item_batch_no = item.batch_no
                        item_exp_date = item.exp_date
                        # Parse exp_date string (e.g. "2026-04" or "2026-04-01") to date object
                        if isinstance(item_exp_date, str):
                            try:
                                from datetime import date as _date

                                parts = item_exp_date.split("-")
                                if len(parts) == 2:
                                    item_exp_date = _date(
                                        int(parts[0]), int(parts[1]), 1
                                    )
                                elif len(parts) == 3:
                                    item_exp_date = _date(
                                        int(parts[0]), int(parts[1]), int(parts[2])
                                    )
                                else:
                                    item_exp_date = None
                            except (ValueError, TypeError):
                                item_exp_date = None
                        item_batch_id = item.batch_id
                        if item_batch_id and (not item_batch_no or not item_exp_date):
                            batch_info = await conn.fetchrow(
                                "SELECT batch_number, expiry_date FROM item_batches WHERE id = $1",
                                UUID(item_batch_id)
                                if isinstance(item_batch_id, str)
                                else item_batch_id,
                            )
                            if batch_info:
                                if not item_batch_no:
                                    item_batch_no = batch_info["batch_number"]
                                if not item_exp_date:
                                    item_exp_date = batch_info["expiry_date"]

                        await conn.execute(
                            """
                            INSERT INTO sales_invoice_items (
                                invoice_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number,
                                batch_id, batch_no, exp_date,
                                tax_code_id, dpp
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
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
                            calc["line_number"],
                            UUID(item_batch_id) if item_batch_id else None,
                            item_batch_no,
                            item_exp_date,
                            UUID(str(item.tax_code_id))
                            if getattr(item, "tax_code_id", None)
                            else None,
                            calc.get("dpp")
                            or (calc["subtotal"] - calc["discount_amount"]),
                        )

                    # Update invoice totals
                    invoice_discount = body.discount_amount or 0
                    if body.discount_percent and body.discount_percent > 0:
                        invoice_discount = _r2(
                            _d(subtotal) * _d(body.discount_percent) / Decimal("100")
                        )

                    total_amount = (
                        subtotal - total_item_discount - invoice_discount + total_tax
                    )

                    await conn.execute(
                        """
                        UPDATE sales_invoices
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """,
                        invoice_id,
                        subtotal,
                        invoice_discount,
                        total_tax,
                        total_amount,
                    )

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

                # Fetch updated invoice for response
                updated = await conn.fetchrow(
                    "SELECT id, invoice_number, total_amount, amount_paid, status, invoice_date, due_date, customer_id, customer_name, created_at FROM sales_invoices WHERE id = $1",
                    invoice_id,
                )
                return {
                    "success": True,
                    "message": "Invoice updated successfully",
                    "data": {
                        "id": str(updated["id"]),
                        "invoice_number": updated["invoice_number"],
                        "total_amount": float(updated["total_amount"] or 0),
                        "amount_paid": float(updated["amount_paid"] or 0),
                        "status": updated["status"] or "draft",
                        "invoice_date": str(updated["invoice_date"])
                        if updated["invoice_date"]
                        else None,
                        "due_date": str(updated["due_date"])
                        if updated["due_date"]
                        else None,
                        "customer_id": str(updated["customer_id"])
                        if updated["customer_id"]
                        else None,
                        "customer_name": updated["customer_name"],
                        "created_at": str(updated["created_at"])
                        if updated["created_at"]
                        else None,
                    },
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
async def post_invoice(
    request: Request, invoice_id: UUID, body: PostInvoiceRequest = None
):
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
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, customer_id, customer_name, total_amount,
                       invoice_date, due_date, status, warehouse_id
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft invoices can be posted"
                )

            # Check if accounting period is open
            await check_period_is_open(conn, ctx["tenant_id"], invoice["invoice_date"])

            # Get invoice items
            items = await conn.fetch(
                """
                SELECT id, item_id, item_code, description, quantity, unit_price, batch_id, batch_no, exp_date
                FROM sales_invoice_items
                WHERE invoice_id = $1
            """,
                invoice_id,
            )

            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"INVOICE:{str(invoice_id)}",
                )

                # Create AR record
                ar_id = await conn.fetchval(
                    """
                    INSERT INTO accounts_receivable (
                        tenant_id, customer_id, customer_name,
                        source_type, source_id, invoice_number,
                        amount, amount_paid,
                        invoice_date, due_date,
                        status
                    ) VALUES ($1, $2::uuid, $3, 'INVOICE', $4, $5, $6, 0, $7, $8, 'OPEN')
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    invoice["customer_id"],
                    invoice["customer_name"],
                    invoice_id,
                    invoice["invoice_number"],
                    invoice["total_amount"],
                    invoice["invoice_date"],
                    invoice["due_date"],
                )

                # Create journal entry (simplified - actual implementation would use AccountingFacade)
                # Debit: Piutang Usaha (1-10300)
                # Credit: Penjualan (4-10100)
                import uuid

                journal_id = uuid.uuid4()
                trace_id = str(uuid.uuid4())

                # Get next journal number using sequence table
                from datetime import date as dt_date

                today = dt_date.today()
                year_month_str = today.strftime("%y%m")
                journal_seq = await conn.fetchval(
                    """
                    INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
                    VALUES ($1, 'JV', $2, $3, 1)
                    ON CONFLICT (tenant_id, prefix, year, month)
                    DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
                    RETURNING last_number
                """,
                    ctx["tenant_id"],
                    today.year,
                    today.month,
                )
                journal_number = f"JV-{year_month_str}-{journal_seq:04d}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        total_debit, total_credit,
                        status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'INVOICE', $6, $7, $8, $8, 'DRAFT', $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    invoice["invoice_date"],
                    f"Faktur Penjualan {invoice['invoice_number']} - {invoice['customer_name']}",
                    invoice_id,
                    trace_id,
                    invoice["total_amount"],
                    ctx["user_id"],
                )
                # Get AR and Sales accounts
                ar_account = {
                    "id": await resolve_account_id(conn, ctx["tenant_id"], "1-10400")
                }
                sales_account = {
                    "id": await resolve_account_id(conn, ctx["tenant_id"], "4-10100")
                }

                # Fetch per-item tax data for proper journal split + DTL
                tax_items = await conn.fetch(
                    """
                    SELECT id, tax_code_id, tax_rate, tax_amount, subtotal, discount_amount, dpp
                    FROM sales_invoice_items
                    WHERE invoice_id = $1 AND COALESCE(tax_amount, 0) > 0
                    """,
                    invoice_id,
                )
                total_tax = sum(float(ti["tax_amount"] or 0) for ti in tax_items)
                subtotal_amount = float(invoice["total_amount"]) - total_tax

                # Resolve PPN Keluaran account
                vat_output_account = {
                    "id": await resolve_account_id(conn, ctx["tenant_id"], "2-10600")
                }

                if ar_account and sales_account:
                    import uuid as _uuid

                    line_number = 1

                    # Line 1: Debit AR = total_amount (inclusive of tax)
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        _uuid.uuid4(),
                        journal_id,
                        line_number,
                        ar_account["id"],
                        invoice["total_amount"],
                        f"Piutang - {invoice['invoice_number']}",
                    )
                    line_number += 1

                    # Line 2: Credit Sales = subtotal (WITHOUT tax)
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        _uuid.uuid4(),
                        journal_id,
                        line_number,
                        sales_account["id"],
                        subtotal_amount,
                        f"Penjualan - {invoice['invoice_number']}",
                    )
                    line_number += 1

                    # Line 3: Credit PPN Keluaran = total_tax
                    vat_journal_line_id = None
                    if total_tax > 0 and vat_output_account.get("id"):
                        vat_journal_line_id = _uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                            VALUES ($1, $2, $3, $4, 0, $5, $6)
                            """,
                            vat_journal_line_id,
                            journal_id,
                            line_number,
                            vat_output_account["id"],
                            total_tax,
                            f"PPN Keluaran - {invoice['invoice_number']}",
                        )
                        line_number += 1
                else:
                    vat_journal_line_id = None
                    warnings.append(
                        "AR account (1-10400) or Sales account (4-10100) not found. Journal lines not created."
                    )

                # Law 20: DRAFT->POSTED triggers hash chain
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # T3: Write document_tax_lines per taxable item
                for ti in tax_items:
                    import uuid as _uuid_dtl
                    _tcid = ti["tax_code_id"]
                    # Fallback: resolve tax_code_id by rate for legacy rows (e.g. from SO to-invoice)
                    if not _tcid:
                        if float(ti["tax_rate"] or 0) <= 0 or float(ti["tax_amount"] or 0) <= 0:
                            continue
                        _tcid = await conn.fetchval(
                            "SELECT id FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND rate=$2 AND is_active=true AND (name ILIKE '%%Keluaran%%' OR name NOT ILIKE '%%Masukan%%') ORDER BY (name ILIKE '%%Keluaran%%') DESC LIMIT 1",
                            ctx["tenant_id"], ti["tax_rate"],
                        )
                        if not _tcid:
                            continue

                    tc_coa = await conn.fetchval(
                        "SELECT coa_id FROM tax_codes WHERE id = $1",
                        _tcid,
                    )
                    dpp_val = float(ti["dpp"] or 0) or (
                        float(ti["subtotal"] or 0) - float(ti["discount_amount"] or 0)
                    )
                    await conn.execute(
                        """
                        INSERT INTO document_tax_lines (
                            id, tenant_id, document_type, document_id, line_item_id,
                            tax_code_id, direction, base_amount, tax_amount,
                            coa_id, journal_line_id
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        """,
                        _uuid_dtl.uuid4(),
                        ctx["tenant_id"],
                        "SALES_INVOICE",
                        invoice_id,
                        ti["id"],
                        _tcid,
                        "output",
                        dpp_val,
                        float(ti["tax_amount"]),
                        tc_coa,
                        vat_journal_line_id,
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
                    product = await conn.fetchrow(
                        """
                        SELECT id, item_code, nama_produk, purchase_price_amount, track_inventory, track_batches
                        FROM products
                        WHERE tenant_id = $1 AND id = $2
                    """,
                        ctx["tenant_id"],
                        item["item_id"],
                    )

                    if not product or not product.get("track_inventory", True):
                        # Skip non-inventory products
                        continue

                    # Get weighted average cost from inventory ledger
                    avg_cost = await conn.fetchval(
                        """
                        SELECT get_weighted_average_cost($1, $2)
                    """,
                        ctx["tenant_id"],
                        item["item_id"],
                    )

                    cost_source = "WEIGHTED_AVG"

                    # Fallback to purchase_price if no inventory history
                    if not avg_cost or avg_cost == 0:
                        avg_cost = product.get("purchase_price_amount", 0) or 0
                        cost_source = "PURCHASE_PRICE"
                        if avg_cost > 0:
                            warnings.append(
                                f"Item {item['item_code'] or product['item_code']}: Using purchase_price as fallback (no cost history)"
                            )

                    if (not avg_cost or avg_cost == 0) and product.get("track_inventory", True):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Tidak bisa post faktur: produk '{product['nama_produk']}' tidak punya "
                                f"riwayat stok / biaya perolehan (WAC=0 dan harga beli=0). "
                                f"Catat penerimaan barang (Bill / Penerimaan Produksi / Opening Stock) terlebih dahulu."
                            )
                        )

                    if avg_cost > 0:
                        quantity = float(item["quantity"])
                        line_cogs = int(quantity * float(avg_cost))
                        total_cogs += line_cogs

                        cogs_items.append(
                            {
                                "item_id": str(item["item_id"]),
                                "item_code": item["item_code"] or product["item_code"],
                                "quantity": quantity,
                                "unit_cost": avg_cost,
                                "total_cost": line_cogs,
                                "cost_source": cost_source,
                            }
                        )

                        # Update sales_invoice_items with cost info
                        await conn.execute(
                            """
                            UPDATE sales_invoice_items
                            SET unit_cost = $2, total_cost = $3,
                                is_inventory_item = true, cost_source = $4
                            WHERE id = $1
                        """,
                            item["id"],
                            avg_cost,
                            line_cogs,
                            cost_source,
                        )

                        # Get current inventory balance for ledger entry
                        current_balance = await conn.fetchval(
                            """
                            SELECT get_inventory_balance($1, $2)
                        """,
                            ctx["tenant_id"],
                            item["item_id"],
                        )

                        new_balance = float(current_balance or 0) - quantity

                        # Resolve warehouse_id for this posting
                        posting_warehouse_id = invoice.get("warehouse_id")
                        if not posting_warehouse_id:
                            posting_warehouse_id = await conn.fetchval(
                                "SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY created_at LIMIT 1",
                                ctx["tenant_id"],
                            )

                        # Resolve batch_id (explicit or FEFO auto-allocation)
                        si_batch_id = item.get("batch_id")
                        if (
                            not si_batch_id
                            and product.get("track_batches")
                            and posting_warehouse_id
                        ):
                            fefo_batches = await conn.fetch(
                                "SELECT * FROM get_available_batches($1, $2, $3, $4, 'FEFO')",
                                ctx["tenant_id"],
                                item["item_id"],
                                posting_warehouse_id,
                                quantity,
                            )
                            if fefo_batches:
                                total_allocated = sum(
                                    float(r["quantity_to_use"]) for r in fefo_batches
                                )
                                if total_allocated >= quantity:
                                    si_batch_id = fefo_batches[0]["batch_id"]
                                else:
                                    raise HTTPException(
                                        400,
                                        f"Stok batch tidak cukup untuk {product.get('nama_produk', item['item_id'])}",
                                    )
                            else:
                                raise HTTPException(
                                    400,
                                    f"Stok batch tidak cukup untuk {product.get('nama_produk', item['item_id'])}",
                                )

                        # Record inventory movement in ledger
                        await conn.execute(
                            """
                            INSERT INTO inventory_ledger (
                                tenant_id, product_id, product_code, product_name,
                                movement_type, movement_date,
                                source_type, source_id, source_number,
                                quantity_in, quantity_out, quantity_balance,
                                unit_cost, total_cost, average_cost,
                                created_by, notes, warehouse_id, batch_id,
                                transaction_unit, transaction_quantity, conversion_factor
                            ) VALUES (
                                $1, $2, $3, $4,
                                'SALE', $5,
                                'SALES_INVOICE', $6, $7,
                                0, $8, $9,
                                $10, $11, $10,
                                $12, $13, $14, $15,
                                $16, $17, $18
                            )
                        """,
                            ctx["tenant_id"],
                            item["item_id"],
                            item["item_code"] or product["item_code"],
                            product["nama_produk"],
                            invoice["invoice_date"],
                            invoice_id,
                            invoice["invoice_number"],
                            quantity,
                            new_balance,
                            avg_cost,
                            line_cogs,
                            ctx["user_id"],
                            f"Sale: {invoice['invoice_number']}",
                            posting_warehouse_id,
                            si_batch_id,
                            transaction_unit_si2,
                            transaction_quantity_si2,
                            float(conversion_factor_si2),
                        )

                        # Batch deduction (if batch tracking)
                        if si_batch_id:
                            bws_row = await conn.fetchrow(
                                """
                                SELECT available_quantity FROM batch_warehouse_stock
                                WHERE batch_id = $1 AND warehouse_id = $2
                                FOR UPDATE
                            """,
                                si_batch_id,
                                posting_warehouse_id,
                            )

                            if (
                                bws_row is None
                                or float(bws_row["available_quantity"]) < quantity
                            ):
                                available = (
                                    float(bws_row["available_quantity"])
                                    if bws_row
                                    else 0
                                )
                                raise HTTPException(
                                    400,
                                    f"Stok batch tidak cukup. Tersedia: {available}, diminta: {quantity}",
                                )

                            await conn.execute(
                                """
                                UPDATE batch_warehouse_stock
                                SET quantity = quantity - $3,
                                    last_movement_date = NOW(), updated_at = NOW()
                                WHERE batch_id = $1 AND warehouse_id = $2
                            """,
                                si_batch_id,
                                posting_warehouse_id,
                                quantity,
                            )

                            # item_batches.current_quantity synced by trg_sync_batch_quantity trigger

                            await conn.execute(
                                """
                                UPDATE item_batches SET status = 'depleted'
                                WHERE id = $1 AND current_quantity <= 0 AND status = 'active'
                            """,
                                si_batch_id,
                            )

                        # Stock is tracked via inventory_ledger, not products.stock_quantity

                # Create COGS journal if there are inventory items
                cogs_journal_id = None
                if total_cogs > 0:
                    cogs_journal_id = uuid.uuid4()
                    cogs_trace_id = str(uuid.uuid4())

                    # Get account IDs
                    hpp_account = {
                        "id": await resolve_account_id(
                            conn, ctx["tenant_id"], "5-10100"
                        )
                    }

                    inventory_account = {
                        "id": await resolve_account_id(
                            conn, ctx["tenant_id"], "1-10600"
                        )
                    }

                    if hpp_account and inventory_account:
                        # Get COGS journal number using sequence
                        cogs_seq = await conn.fetchval(
                            """
                            INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
                            VALUES ($1, 'COGS', $2, $3, 1)
                            ON CONFLICT (tenant_id, prefix, year, month)
                            DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
                            RETURNING last_number
                        """,
                            ctx["tenant_id"],
                            today.year,
                            today.month,
                        )
                        cogs_journal_number = f"COGS-{year_month_str}-{cogs_seq:04d}"

                        # Create COGS journal entry
                        await conn.execute(
                            """
                            INSERT INTO journal_entries (
                                id, tenant_id, journal_number, journal_date,
                                description, source_type, source_id, trace_id,
                                total_debit, total_credit,
                                status, created_by
                            ) VALUES ($1, $2, $3, $4, $5, 'SALES_INVOICE_COGS', $6, $7, $8, $8, 'DRAFT', $9)
                        """,
                            cogs_journal_id,
                            ctx["tenant_id"],
                            cogs_journal_number,
                            invoice["invoice_date"],
                            f"HPP {invoice['invoice_number']} - {invoice['customer_name']}",
                            invoice_id,
                            cogs_trace_id,
                            total_cogs,
                            ctx["user_id"],
                        )

                        # Journal lines: Dr. HPP (5-10100), Cr. Inventory (1-10400)
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                journal_id, account_id, memo,
                                debit, credit, line_number
                            ) VALUES
                            ($1, $2, 'HPP Barang Dagang', $3, 0, 1),
                            ($1, $4, 'Persediaan Barang Dagang', 0, $3, 2)
                        """,
                            cogs_journal_id,
                            hpp_account["id"],
                            total_cogs,
                            inventory_account["id"],
                        )

                        # Law 20: DRAFT->POSTED triggers hash chain
                        await conn.execute(
                            "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                            cogs_journal_id,
                        )

                        logger.info(
                            f"COGS journal created: {cogs_journal_id}, amount: {total_cogs}"
                        )
                    else:
                        warnings.append(
                            "COGS accounts (5-10100 or 1-10400) not found. COGS journal not created."
                        )

                # Update invoice status and COGS info
                await conn.execute(
                    """
                    UPDATE sales_invoices
                    SET status = 'posted', operational_status = 'SENT', accounting_status = 'POSTED', ar_id = $2, journal_id = $3,
                        cogs_journal_id = $4, total_cogs = $5::bigint, cogs_posted_at = CASE WHEN $5::bigint > 0 THEN NOW() ELSE NULL END,
                        posted_at = NOW(), posted_by = $6, updated_at = NOW()
                    WHERE id = $1
                """,
                    invoice_id,
                    ar_id,
                    journal_id,
                    cogs_journal_id,
                    total_cogs,
                    ctx["user_id"],
                )

                logger.info(
                    f"Invoice posted: {invoice_id}, AR: {ar_id}, COGS: {total_cogs}"
                )

                response_data = {
                    "id": str(invoice_id),
                    "ar_id": str(ar_id),
                    "journal_id": str(journal_id),
                    "total_cogs": total_cogs,
                }

                if cogs_journal_id:
                    response_data["cogs_journal_id"] = str(cogs_journal_id)
                    response_data["cogs_items"] = cogs_items

                if warnings:
                    response_data["warnings"] = warnings

                return {
                    "success": True,
                    "message": "Invoice posted successfully"
                    + (" with COGS" if total_cogs > 0 else ""),
                    "data": response_data,
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
async def record_payment(
    request: Request, invoice_id: UUID, body: InvoicePaymentCreate
):
    """
    Record payment for a sales invoice.
    Creates receive_payments + receive_payment_allocations (ARAP Rule 1).
    Journal source_type='RECEIVE_PAYMENT', source_id=receive_payments.id
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"INVOICE_PAYMENT:{str(invoice_id)}",
                )

                # Law 14: Idempotency check
                idem_key = get_idempotency_key(
                    request, f"INVOICE_PAYMENT:{invoice_id}:{body.amount}"
                )
                existing_idem = await conn.fetchrow(
                    "SELECT result FROM idempotency_keys WHERE tenant_id = $1 AND key = $2 AND expires_at > NOW()",
                    ctx["tenant_id"],
                    idem_key,
                )
                if existing_idem and existing_idem["result"]:
                    import json as json_mod

                    return json_mod.loads(existing_idem["result"])

                # Fetch invoice (FOR UPDATE)
                invoice = await conn.fetchrow(
                    """
                    SELECT id, invoice_number, customer_id, customer_name,
                           status, total_amount, amount_paid, ar_id
                    FROM sales_invoices
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                """,
                    invoice_id,
                    ctx["tenant_id"],
                )

                if not invoice:
                    raise HTTPException(status_code=404, detail="Invoice not found")
                if invoice["status"] not in ("posted", "partial", "overdue"):
                    raise HTTPException(
                        status_code=400,
                        detail="Invoice must be posted before recording payment",
                    )

                # Law 16: journal-derived remaining (same CTE as original)
                journal_remaining = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE je.status = 'POSTED'
                        AND coa.account_code = '1-10400'
                        AND je.tenant_id = $2
                        AND (
                            (je.source_type = 'INVOICE' AND je.source_id = $1)
                            OR (je.source_type IN ('RECEIVE_PAYMENT', 'PAYMENT_RECEIVED') AND EXISTS (
                                SELECT 1 FROM receive_payment_allocations rpa
                                WHERE rpa.invoice_id = $1 AND rpa.payment_id = je.source_id
                            ))
                            OR (je.source_type = 'PAYMENT_RECEIVED'
                                AND je.description LIKE '%%' || (SELECT invoice_number FROM sales_invoices WHERE id = $1) || '%%'
                                AND NOT EXISTS(
                                    SELECT 1 FROM receive_payment_allocations rpa2
                                    WHERE rpa2.payment_id = je.source_id AND rpa2.tenant_id = $2
                                ))
                            OR (je.source_type = 'CREDIT_NOTE' AND EXISTS (
                                SELECT 1 FROM credit_note_applications cna
                                WHERE cna.invoice_id = $1 AND cna.credit_note_id = je.source_id
                            ))
                            OR (je.source_type = 'DEPOSIT_APPLICATION' AND EXISTS (
                                SELECT 1 FROM customer_deposit_applications cda
                                WHERE cda.invoice_id = $1 AND cda.deposit_id = je.source_id
                            ))
                            OR (je.source_type = 'INVOICE_REVERSAL' AND je.source_id = $1)
                            OR (je.id IN (
                                SELECT sip.journal_id FROM sales_invoice_payments sip
                                WHERE sip.invoice_id = $1
                            ))
                        )
                """,
                    invoice_id,
                    ctx["tenant_id"],
                )

                remaining = int(journal_remaining or 0)
                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Payment amount exceeds remaining balance of Rp {remaining:,}",
                    )

                # --- Resolve bank account ---
                # Frontend sends account_id (CoA UUID). May also send bank_account_id.
                # bank_accounts schema: id (UUID PK), coa_id (UUID FK to CoA), account_name (varchar)
                account_uuid = UUID(body.account_id)
                bank_account_uuid = None
                bank_account_name = ""
                bank_coa_id = account_uuid  # fallback: treat as CoA directly

                if body.bank_account_id:
                    bank_row = await conn.fetchrow(
                        "SELECT id, coa_id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                        UUID(body.bank_account_id),
                        ctx["tenant_id"],
                    )
                    if bank_row:
                        bank_account_uuid = bank_row["id"]
                        bank_account_name = bank_row["account_name"]
                        bank_coa_id = bank_row["coa_id"]
                else:
                    # Try: account_id is coa_id → reverse lookup bank_account
                    bank_row = await conn.fetchrow(
                        "SELECT id, coa_id, account_name FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2",
                        account_uuid,
                        ctx["tenant_id"],
                    )
                    if bank_row:
                        bank_account_uuid = bank_row["id"]
                        bank_account_name = bank_row["account_name"]
                        bank_coa_id = bank_row["coa_id"]
                    else:
                        # Try: account_id IS a bank_accounts.id
                        bank_row = await conn.fetchrow(
                            "SELECT id, coa_id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                            account_uuid,
                            ctx["tenant_id"],
                        )
                        if bank_row:
                            bank_account_uuid = bank_row["id"]
                            bank_account_name = bank_row["account_name"]
                            bank_coa_id = bank_row["coa_id"]

                if not bank_account_uuid:
                    raise HTTPException(
                        status_code=400,
                        detail="Could not resolve bank account from account_id",
                    )

                # Resolve AR account (Law 27)
                ar_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], "1-10400"
                )

                # Map payment_method: receive_payments CHECK allows only 'cash' or 'bank_transfer'
                pm = body.payment_method
                if pm not in ("cash", "bank_transfer"):
                    pm = "bank_transfer"

                # Generate payment number via DB function (same as golden pattern)
                payment_number = await conn.fetchval(
                    "SELECT generate_receive_payment_number($1)", ctx["tenant_id"]
                )

                import uuid as uuid_module

                pay_amount = body.amount  # int from InvoicePaymentCreate schema

                # === INSERT receive_payments ===
                # NOT NULL cols: id, tenant_id, payment_number, customer_name, payment_date,
                #   payment_method, bank_account_id, bank_account_name, source_type,
                #   total_amount, allocated_amount, unapplied_amount, discount_amount
                rp_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO receive_payments (
                        id, tenant_id, payment_number, customer_id, customer_name,
                        payment_date, payment_method, bank_account_id, bank_account_name,
                        source_type, total_amount, allocated_amount, unapplied_amount,
                        discount_amount, reference_number, notes, status, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        'cash', $10, $11, 0,
                        0, $12, $13, 'draft', $14
                    )
                """,
                    rp_id,
                    ctx["tenant_id"],
                    payment_number,
                    str(invoice["customer_id"]) if invoice["customer_id"] else None,
                    invoice["customer_name"],
                    body.payment_date,
                    pm,
                    bank_account_uuid,
                    bank_account_name,
                    pay_amount,
                    pay_amount,
                    body.reference,
                    body.notes,
                    ctx["user_id"],
                )

                # === INSERT receive_payment_allocations ===
                # NOT NULL cols: tenant_id, payment_id, invoice_id, invoice_number,
                #   invoice_amount, remaining_before, amount_applied, remaining_after
                await conn.execute(
                    """
                    INSERT INTO receive_payment_allocations (
                        tenant_id, payment_id, invoice_id, invoice_number,
                        invoice_amount, remaining_before, amount_applied, remaining_after
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                    ctx["tenant_id"],
                    rp_id,
                    invoice_id,
                    invoice["invoice_number"],
                    float(invoice["total_amount"]),
                    remaining,
                    pay_amount,
                    remaining - pay_amount,
                )

                # === Create journal: Dr. Bank, Cr. AR ===
                # Law 20: DRAFT → lines → POSTED
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, $2)", ctx["tenant_id"], "RCV"
                )
                if not journal_number:
                    journal_number = f"JRN-PAY-{rp_id}"

                inv_number = invoice["invoice_number"]

                # source_type='RECEIVE_PAYMENT', source_id=rp_id (UUID)
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'RECEIVE_PAYMENT', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.payment_date,
                    f"Penerimaan Pembayaran Faktur {inv_number}",
                    rp_id,
                    str(trace_id),
                    pay_amount,
                    ctx["user_id"],
                )

                # Dr. Bank/Kas (line 1)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    bank_coa_id,
                    pay_amount,
                    f"Terima Pembayaran - {inv_number}",
                )

                # Cr. Piutang / AR (line 2)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    ar_account_id,
                    pay_amount,
                    f"Pelunasan Piutang - {inv_number}",
                )

                # DRAFT → POSTED (triggers hash chain)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Link journal to receive_payments + mark posted
                await conn.execute(
                    """
                    UPDATE receive_payments
                    SET journal_id = $1, journal_number = $2, status = 'posted',
                        posted_at = NOW(), posted_by = $3,
                        operational_status = 'CONFIRMED', accounting_status = 'POSTED'
                    WHERE id = $4
                """,
                    journal_id,
                    journal_number,
                    ctx["user_id"],
                    rp_id,
                )

                # Update invoice cache (Law 21: write-side only)
                await conn.execute(
                    """
                    UPDATE sales_invoices
                    SET amount_paid = amount_paid + $1,
                        status = CASE WHEN total_amount <= (amount_paid + $1) THEN 'paid' ELSE 'partial' END,
                        updated_at = NOW()
                    WHERE id = $2 AND tenant_id = $3
                """,
                    pay_amount,
                    invoice_id,
                    ctx["tenant_id"],
                )

                # Update AR cache if exists
                if invoice["ar_id"]:
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET amount_paid = amount_paid + $2,
                            status = CASE WHEN amount - (amount_paid + $2) <= 0 THEN 'PAID' ELSE 'PARTIAL' END,
                            updated_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["ar_id"],
                        pay_amount,
                    )

                # Bank transaction (BankSync Rule 1: atomic journal + bank_txn)
                if bank_account_uuid:
                    bank_tx_id = uuid_module.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, description,
                            payee_payer, journal_id, created_by
                        ) VALUES ($1, $2, $3, $4, 'payment_received', $5, 0, 'invoice', $6, $7, $8, $9, $10)
                    """,
                        bank_tx_id,
                        ctx["tenant_id"],
                        bank_account_uuid,
                        body.payment_date,
                        pay_amount,
                        invoice_id,
                        f"Payment received for {inv_number}",
                        body.reference or "Customer Payment",
                        journal_id,
                        ctx["user_id"],
                    )
                    await conn.execute(
                        "UPDATE receive_payments SET bank_transaction_id = $1 WHERE id = $2",
                        bank_tx_id,
                        rp_id,
                    )

                # Law 14: Store idempotency
                import json as json_mod

                result_payload = {
                    "success": True,
                    "message": "Payment recorded successfully",
                    "data": {
                        "status": "posted",
                        "id": str(rp_id),
                        "invoice_id": str(invoice_id),
                        "amount": pay_amount,
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "payment_number": payment_number,
                    },
                }
                await conn.execute(
                    """INSERT INTO idempotency_keys (key, tenant_id, source_type, result, result_status, expires_at)
                    VALUES ($1, $2, 'RECEIVE_PAYMENT', $3, 'SUCCESS', NOW() + interval '24 hours')
                    ON CONFLICT (tenant_id, key) DO NOTHING""",
                    idem_key,
                    ctx["tenant_id"],
                    json_mod.dumps(result_payload, default=str),
                )

                logger.info(
                    f"Payment recorded via receive_payments: {rp_id} for invoice {invoice_id}, journal={journal_id}"
                )
                return result_payload

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
    """
    Void an invoice following Iron Laws:
    - Law 2: Journal Immutability - creates REVERSAL journals, not delete
    - Law 3: Append-Only - inventory restored via new ledger entry
    - Law 4: Double-Entry - all reversals must balance
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get full invoice data including COGS info
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, customer_name, total_amount, invoice_date,
                       status, ar_id, journal_id, cogs_journal_id, total_cogs
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] == "void":
                raise HTTPException(status_code=400, detail="Invoice is already voided")

            # Pure Ledger: check if invoice has journal-based payments (Law 16)
            journal_paid = await conn.fetchval(
                """
                SELECT
                    COALESCE((SELECT SUM(rpa.amount_applied)
                        FROM receive_payment_allocations rpa
                        JOIN receive_payments rp ON rp.id = rpa.payment_id
                        WHERE rpa.invoice_id = $1 AND rpa.tenant_id = $2
                          AND rp.status = 'posted' AND rp.journal_id IS NOT NULL), 0)
                    + COALESCE((SELECT SUM(sip_jl.credit)
                        FROM sales_invoice_payments sip
                        JOIN journal_entries sip_je ON sip_je.id = sip.journal_id
                        JOIN journal_lines sip_jl ON sip_jl.journal_id = sip_je.id
                        JOIN chart_of_accounts sip_coa ON sip_coa.id = sip_jl.account_id
                        WHERE sip.invoice_id = $1 AND sip_je.status = 'POSTED'
                          AND sip_coa.account_code LIKE '1-104%%'), 0)
                    + COALESCE((SELECT SUM(jl5.credit)
                        FROM journal_lines jl5
                        JOIN journal_entries je5 ON je5.id = jl5.journal_id
                        JOIN chart_of_accounts coa5 ON coa5.id = jl5.account_id
                        WHERE je5.source_type = 'PAYMENT_RECEIVED'
                          AND je5.tenant_id = $2 AND je5.status = 'POSTED'
                          AND coa5.account_code LIKE '1-104%%'
                          AND je5.description LIKE '%%' || (SELECT invoice_number FROM sales_invoices WHERE id = $1) || '%%'
                          AND NOT EXISTS(
                              SELECT 1 FROM receive_payment_allocations rpa5
                              WHERE rpa5.payment_id = je5.source_id AND rpa5.tenant_id = $2
                          )), 0)
            """,
                invoice_id,
                ctx["tenant_id"],
            )
            if (journal_paid or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot void invoice with payments. Refund first.",
                )

            # Check if period is open for void date
            from datetime import date as dt_date

            today = dt_date.today()
            await check_period_is_open(conn, ctx["tenant_id"], today)

            async with conn.transaction():
                import uuid

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"INVOICE_VOID:{str(invoice_id)}",
                )

                year_month_str = today.strftime("%y%m")
                reversal_journal_id = None
                cogs_reversal_journal_id = None

                # ============================================================
                # 1. Create REVERSAL Journal for AR (if posted)
                # Iron Law 2: Journal Immutability - REVERSAL, not delete
                # ============================================================
                if invoice["journal_id"]:
                    reversal_journal_id = uuid.uuid4()
                    trace_id = str(uuid.uuid4())

                    # Get next journal number
                    rev_seq = await conn.fetchval(
                        """
                        INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
                        VALUES ($1, 'REV', $2, $3, 1)
                        ON CONFLICT (tenant_id, prefix, year, month)
                        DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
                        RETURNING last_number
                    """,
                        ctx["tenant_id"],
                        today.year,
                        today.month,
                    )
                    rev_journal_number = f"REV-{year_month_str}-{rev_seq:04d}"

                    # Create reversal journal entry
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            total_debit, total_credit,
                            status, created_by, reversal_of_id, reversal_reason
                        ) VALUES ($1, $2, $3, $4, $5, 'INVOICE_REVERSAL', $6, $7, $8, $8, 'DRAFT', $9, $10, $11)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        rev_journal_number,
                        today,
                        f"VOID: Faktur {invoice['invoice_number']} - {invoice['customer_name']}",
                        invoice_id,
                        trace_id,
                        invoice["total_amount"],
                        ctx["user_id"],
                        invoice["journal_id"],  # Reference to original
                        body.reason,
                    )

                    # Get account IDs
                    ar_account = {
                        "id": await resolve_account_id(
                            conn, ctx["tenant_id"], "1-10400"
                        )
                    }
                    sales_account = {
                        "id": await resolve_account_id(
                            conn, ctx["tenant_id"], "4-10100"
                        )
                    }

                    if ar_account and sales_account:
                        # REVERSAL: Dr. Sales (reverse of Cr), Cr. AR (reverse of Dr)
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (journal_id, account_id, debit, credit, memo, line_number)
                            VALUES
                            ($1, $2, $3, 0, $4, 1),
                            ($1, $5, 0, $3, $4, 2)
                        """,
                            reversal_journal_id,
                            sales_account["id"],  # Dr. Sales
                            invoice["total_amount"],
                            f"VOID: Faktur {invoice['invoice_number']}",
                            ar_account["id"],  # Cr. AR
                        )

                    # Law 20: DRAFT->POSTED triggers hash chain
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # Mark original journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["journal_id"],
                        reversal_journal_id,
                    )

                    logger.info(f"AR reversal journal created: {reversal_journal_id}")

                # ============================================================
                # 2. Create REVERSAL Journal for COGS (if exists)
                # Iron Law 2: Journal Immutability - REVERSAL, not delete
                # ============================================================
                if (
                    invoice["cogs_journal_id"]
                    and invoice["total_cogs"]
                    and invoice["total_cogs"] > 0
                ):
                    cogs_reversal_journal_id = uuid.uuid4()
                    cogs_trace_id = str(uuid.uuid4())

                    # Get next COGS reversal number
                    cogs_rev_seq = await conn.fetchval(
                        """
                        INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
                        VALUES ($1, 'COGS-REV', $2, $3, 1)
                        ON CONFLICT (tenant_id, prefix, year, month)
                        DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
                        RETURNING last_number
                    """,
                        ctx["tenant_id"],
                        today.year,
                        today.month,
                    )
                    cogs_rev_number = f"COGS-REV-{year_month_str}-{cogs_rev_seq:04d}"

                    # Create COGS reversal journal
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            total_debit, total_credit,
                            status, created_by, reversal_of_id, reversal_reason
                        ) VALUES ($1, $2, $3, $4, $5, 'SALES_INVOICE_COGS_REVERSAL', $6, $7, $8, $8, 'DRAFT', $9, $10, $11)
                    """,
                        cogs_reversal_journal_id,
                        ctx["tenant_id"],
                        cogs_rev_number,
                        today,
                        f"VOID HPP: {invoice['invoice_number']} - {invoice['customer_name']}",
                        invoice_id,
                        cogs_trace_id,
                        invoice["total_cogs"],
                        ctx["user_id"],
                        invoice["cogs_journal_id"],  # Reference to original
                        body.reason,
                    )

                    # Get account IDs
                    hpp_account = {
                        "id": await resolve_account_id(
                            conn, ctx["tenant_id"], "5-10100"
                        )
                    }
                    inventory_account = {
                        "id": await resolve_account_id(
                            conn, ctx["tenant_id"], "1-10600"
                        )
                    }

                    if hpp_account and inventory_account:
                        # REVERSAL: Dr. Inventory (reverse of Cr), Cr. HPP (reverse of Dr)
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (journal_id, account_id, debit, credit, memo, line_number)
                            VALUES
                            ($1, $2, $3, 0, $4, 1),
                            ($1, $5, 0, $3, $4, 2)
                        """,
                            cogs_reversal_journal_id,
                            inventory_account["id"],  # Dr. Inventory
                            invoice["total_cogs"],
                            f"VOID HPP: {invoice['invoice_number']}",
                            hpp_account["id"],  # Cr. HPP
                        )

                    # Law 20: DRAFT->POSTED triggers hash chain
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        cogs_reversal_journal_id,
                    )

                    # Mark original COGS journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["cogs_journal_id"],
                        cogs_reversal_journal_id,
                    )

                    logger.info(
                        f"COGS reversal journal created: {cogs_reversal_journal_id}"
                    )

                # ============================================================
                # 3. Restore Inventory via shared helper
                # Iron Law 3: Append-Only - new entry to restore
                # Uses record_inventory_reversal() (milkyhoop-inventory Rule 9)
                # ============================================================
                from ..services.inventory_helpers import record_inventory_reversal

                await record_inventory_reversal(
                    conn,
                    ctx["tenant_id"],
                    source_type="SALES_INVOICE",
                    source_id=invoice_id,
                    reversal_journal_id=cogs_reversal_journal_id
                    or (reversal_journal_id if reversal_journal_id else invoice_id),
                    created_by=ctx["user_id"],
                    reversal_date=today,
                    notes_prefix="VOID",
                )

                # ============================================================
                # 3.5. Reverse Bank Transactions (BankSync Rule 3)
                # ============================================================
                payment_bank_txns = await conn.fetch(
                    """
                    SELECT bt.id, bt.bank_account_id, bt.amount, bt.transaction_type,
                           bt.description, bt.journal_id
                    FROM bank_transactions bt
                    JOIN sales_invoice_payments sip ON sip.journal_id = bt.journal_id
                    WHERE sip.invoice_id = $1 AND bt.tenant_id = $2
                """,
                    invoice_id,
                    ctx["tenant_id"],
                )

                for bt in payment_bank_txns:
                    reversal_bt_id = uuid.uuid4()
                    reversed_type = (
                        "CREDIT" if bt["transaction_type"] == "DEBIT" else "DEBIT"
                    )
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, description,
                            journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, 0, 'invoice_void', $7, $8, $9, $10)
                    """,
                        reversal_bt_id,
                        ctx["tenant_id"],
                        bt["bank_account_id"],
                        today,
                        reversed_type,
                        -bt["amount"],
                        invoice_id,
                        f"VOID: Reversal of {bt['description']}",
                        reversal_journal_id,
                        ctx["user_id"],
                    )
                    logger.info(
                        f"Bank transaction reversed: {bt['id']} -> {reversal_bt_id}"
                    )

                # ============================================================
                # 4. Update AR status to VOID
                # ============================================================
                if invoice["ar_id"]:
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET status = 'VOID', updated_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["ar_id"],
                    )

                # ============================================================
                # 5. Update invoice status to void
                # ============================================================
                await conn.execute(
                    """
                    UPDATE sales_invoices
                    SET status = 'void', operational_status = 'VOID', accounting_status = 'REVERSED', voided_at = NOW(), voided_reason = $2,
                        updated_at = NOW()
                    WHERE id = $1
                """,
                    invoice_id,
                    body.reason,
                )

                # Clean up document_tax_lines on void
                await conn.execute(
                    "DELETE FROM document_tax_lines WHERE document_id = $1 AND tenant_id = $2",
                    invoice_id, ctx["tenant_id"],
                )

                logger.info(f"Invoice voided: {invoice_id}, reason: {body.reason}")

                return {
                    "success": True,
                    "message": "Invoice voided successfully with reversal journals",
                    "data": {
                        "status": "draft",
                        "id": str(invoice_id),
                        "reversal_journal_id": str(reversal_journal_id)
                        if reversal_journal_id
                        else None,
                        "cogs_reversal_journal_id": str(cogs_reversal_journal_id)
                        if cogs_reversal_journal_id
                        else None,
                    },
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
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, status
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Guard: Cannot delete voided invoices
            if invoice["status"] == "void":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete voided invoice",
                )

            # Guard: Cannot delete non-draft invoices
            if invoice["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete posted invoice. Use void instead.",
                )

            # Delete (cascade will handle items)
            await conn.execute("DELETE FROM sales_invoices WHERE id = $1", invoice_id)

            logger.info(f"Invoice deleted: {invoice_id}")

            return {
                "success": True,
                "message": "Invoice deleted successfully",
                "data": {
                    "status": "draft",
                    "id": str(invoice_id),
                    "invoice_number": invoice["invoice_number"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete invoice")


# =============================================================================
# INVOICE HISTORY (Audit Trail)
# =============================================================================
@router.get("/{invoice_id}/history")
async def get_invoice_history(
    request: Request,
    invoice_id: UUID,
    limit: int = Query(50, ge=1, le=200),
):
    """Get audit history for a sales invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify invoice exists and belongs to tenant
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get history from audit_logs
            # Note: audit_logs table uses camelCase columns and metadata JSONB
            rows = await conn.fetch(
                """
                SELECT id, "createdAt", "eventType", "userId", metadata
                FROM audit_logs
                WHERE metadata->>'entity_type' = 'SALES_INVOICE'
                  AND metadata->>'entity_id' = $1::text
                ORDER BY "createdAt" DESC
                LIMIT $2
            """,
                str(invoice_id),
                limit,
            )

            history = []
            for row in rows:
                metadata = row["metadata"] or {}
                changes = metadata.get("changes")

                history.append(
                    {
                        "id": str(row["id"]),
                        "action": row["eventType"] or metadata.get("action", "UNKNOWN"),
                        "user": {
                            "id": row["userId"] or "",
                            "name": metadata.get("user_name", "System"),
                        },
                        "changes": changes,
                        "created_at": row["createdAt"].isoformat()
                        if row["createdAt"]
                        else None,
                    }
                )

            return {"success": True, "data": history}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice history {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice history")


# =============================================================================
# GENERATE PDF
# =============================================================================
from io import BytesIO
from datetime import datetime, timedelta
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service
from ..services.storage_service import get_storage_service
import base64
from pathlib import Path as _Path


@router.get("/{invoice_id}/pdf")
async def get_invoice_pdf(
    request: Request,
    invoice_id: UUID,
    format: Literal["url", "inline"] = Query(
        "url",
        description="Response format: 'url' returns presigned URL, 'inline' returns PDF bytes",
    ),
):
    """
    Generate PDF for a sales invoice.

    **Format options:**
    - url (default): Returns presigned URL for download/share (expires in 1 hour)
    - inline: Returns PDF bytes directly for browser preview

    **Usage:**
    - For download button: use ?format=url and redirect to returned URL
    - For inline preview: use ?format=inline and embed in iframe/viewer
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Fetch invoice with full details
            invoice = await conn.fetchrow(
                """
                SELECT * FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Fetch items
            items = await conn.fetch(
                """
                SELECT * FROM sales_invoice_items
                WHERE invoice_id = $1 ORDER BY line_number
            """,
                invoice_id,
            )

            # Pure Ledger: derive amount_paid via compute_ar_outstanding() DB function
            pdf_ar_row = await conn.fetchrow(
                """
                SELECT paid_amount, outstanding
                FROM compute_ar_outstanding($1)
                WHERE invoice_id = $2
            """,
                ctx["tenant_id"],
                invoice_id,
            )
            # If no row from function: invoice is fully paid (outstanding=0) or draft/void
            if pdf_ar_row:
                pdf_amount_paid = float(
                    invoice["total_amount"] - pdf_ar_row["outstanding"]
                )
            elif invoice["status"] in ("paid",):
                pdf_amount_paid = float(invoice["total_amount"])
            else:
                pdf_amount_paid = 0

            # Fetch tenant info for PDF header
            tenant_row = await conn.fetchrow(
                'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
                ctx["tenant_id"],
            )
            tenant_info = (
                {
                    "name": tenant_row["display_name"]
                    if tenant_row
                    else ctx["tenant_id"],
                    "address": tenant_row["address"] if tenant_row else None,
                    "phone": tenant_row["phone"] if tenant_row else None,
                    "logo_url": tenant_row["logo_url"] if tenant_row else None,
                }
                if tenant_row
                else {
                    "name": ctx["tenant_id"],
                    "address": None,
                    "phone": None,
                    "logo_url": None,
                }
            )

            # Resolve logo to base64 data URI for PDF embedding
            _logo_data = None
            _logo_filename = tenant_info.get("logo_url")
            if _logo_filename:
                _logo_path = (
                    _Path(__file__).parent.parent / "static" / "logos" / _logo_filename
                )
                if _logo_path.exists():
                    with open(_logo_path, "rb") as _lf:
                        _logo_b64 = base64.b64encode(_lf.read()).decode()
                    _logo_data = f"data:image/png;base64,{_logo_b64}"
            tenant_info["logo_data"] = _logo_data
            # Convert to dict for template
            invoice_data = {
                "id": str(invoice["id"]),
                "invoice_number": invoice["invoice_number"],
                "customer_id": str(invoice["customer_id"])
                if invoice["customer_id"]
                else None,
                "customer_name": invoice["customer_name"],
                "invoice_date": invoice["invoice_date"].isoformat()
                if invoice["invoice_date"]
                else None,
                "due_date": invoice["due_date"].isoformat()
                if invoice["due_date"]
                else None,
                "ref_no": invoice["ref_no"],
                "notes": invoice["notes"],
                "subtotal": invoice["subtotal"],
                "discount_percent": float(invoice["discount_percent"] or 0),
                "discount_amount": invoice["discount_amount"],
                "tax_rate": float(invoice["tax_rate"] or 0),
                "tax_amount": invoice["tax_amount"],
                "total_amount": invoice["total_amount"],
                "amount_paid": pdf_amount_paid,
                "status": invoice["status"],
                "tenant": tenant_info,
                "items": [
                    {
                        "id": str(item["id"]),
                        "item_code": item["item_code"],
                        "description": item["description"],
                        "quantity": float(item["quantity"]),
                        "unit": item["unit"],
                        "unit_price": item["unit_price"],
                        "discount_percent": float(item["discount_percent"] or 0),
                        "discount_amount": item["discount_amount"],
                        "tax_rate": float(item["tax_rate"] or 0),
                        "tax_amount": item["tax_amount"],
                        "subtotal": item["subtotal"],
                        "total": item["total"],
                        "line_number": item["line_number"],
                        "batch_no": item["batch_no"],
                        "exp_date": item["exp_date"],
                        "product_name": item["description"],
                    }
                    for item in items
                ],
            }

        # Generate PDF
        pdf_service = get_pdf_service()
        pdf_bytes = pdf_service.generate_sales_invoice_pdf(invoice_data)

        # Generate filename
        invoice_num = invoice["invoice_number"] or str(invoice_id)[:8]
        filename = f"Faktur-{invoice_num}.pdf"

        if format == "inline":
            return StreamingResponse(
                BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "private, max-age=300",
                },
            )

        # Upload to storage and return presigned URL
        storage = get_storage_service()
        file_path = f"{ctx['tenant_id']}/invoices/{invoice_id}.pdf"

        url = await storage.upload_bytes(
            content=pdf_bytes,
            file_path=file_path,
            content_type="application/pdf",
            metadata={"invoice_id": str(invoice_id), "invoice_number": invoice_num},
        )

        # Calculate expiry
        expires_at = datetime.utcnow() + timedelta(seconds=storage.config.url_expiry)

        return {
            "success": True,
            "data": {
                "status": "draft",
                "url": url,
                "expires_at": expires_at.isoformat() + "Z",
                "filename": filename,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error generating PDF for invoice {invoice_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to generate PDF")


# =============================================================================
# INVOICE ACTIVITY LOG (Tab Aktivitas)
# =============================================================================
@router.get("/{invoice_id}/activity")
async def get_invoice_activity(
    request: Request,
    invoice_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Get activity log for a sales invoice — matches Bills pattern exactly."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, status, total_amount, created_at, posted_at, voided_at,
                       voided_reason, created_by, posted_by
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )
            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Helper: resolve user name from user_id (table is "User" with capital U)
            async def resolve_actor(user_id):
                if not user_id:
                    return None
                try:
                    user = await conn.fetchrow(
                        'SELECT name, fullname FROM "User" WHERE id = $1',
                        str(user_id),
                    )
                    if user:
                        return user["fullname"] or user["name"] or None
                    return None
                except Exception:
                    return None

            activities = []

            # 1. Created
            created_actor = await resolve_actor(invoice["created_by"])
            activities.append(
                {
                    "id": f"{invoice_id}-created",
                    "type": "created",
                    "description": "Faktur dibuat",
                    "actor_name": created_actor,
                    "timestamp": invoice["created_at"].isoformat()
                    if invoice["created_at"]
                    else None,
                    "amount": float(invoice["total_amount"])
                    if invoice["total_amount"]
                    else None,
                    "details": f"Invoice #{invoice['invoice_number']}",
                }
            )

            # 2. Posted (diterbitkan)
            if invoice["posted_at"]:
                posted_actor = await resolve_actor(invoice["posted_by"])
                activities.append(
                    {
                        "id": f"{invoice_id}-posted",
                        "type": "status_changed",
                        "description": "Faktur diterbitkan",
                        "actor_name": posted_actor,
                        "timestamp": invoice["posted_at"].isoformat(),
                        "old_value": "draft",
                        "new_value": "posted",
                    }
                )

            # 3. Voided (dibatalkan)
            if invoice["voided_at"]:
                activities.append(
                    {
                        "id": f"{invoice_id}-voided",
                        "type": "voided",
                        "description": "Faktur dibatalkan",
                        "actor_name": None,
                        "timestamp": invoice["voided_at"].isoformat(),
                        "details": invoice["voided_reason"] or None,
                    }
                )

            # 4. Payments from receive_payments (not deprecated sales_invoice_payments)
            payments = await conn.fetch(
                """
                SELECT rp.id, rp.total_amount as amount, rp.payment_date, rp.payment_method,
                       rp.created_at, rp.created_by,
                       ba.account_name as bank_account_name
                FROM receive_payments rp
                LEFT JOIN receive_payment_allocations rpa ON rpa.payment_id = rp.id
                LEFT JOIN bank_accounts ba ON ba.id = rp.bank_account_id
                WHERE rpa.invoice_id = $1 AND rp.status = 'posted'
                ORDER BY rp.created_at DESC
            """,
                invoice_id,
            )
            for p in payments:
                p_actor = await resolve_actor(p["created_by"])
                activities.append(
                    {
                        "id": f"payment-{p['id']}",
                        "type": "payment",
                        "description": f"Pembayaran {'tunai' if p['payment_method'] == 'cash' else 'transfer'}",
                        "actor_name": p_actor,
                        "timestamp": p["created_at"].isoformat()
                        if p["created_at"]
                        else None,
                        "amount": float(p["amount"]) if p["amount"] else None,
                        "payment_method": p["payment_method"],
                        "bank_account_name": p["bank_account_name"],
                    }
                )

            # Sort by timestamp descending
            activities.sort(key=lambda x: x["timestamp"] or "", reverse=True)

            total = len(activities)
            paginated = activities[offset : offset + limit]

            return {
                "activities": paginated,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice activity {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice activity")


# =============================================================================
# INVOICE JOURNAL ENTRIES (Tab Jurnal)
# =============================================================================
@router.get("/{invoice_id}/journals")
async def get_invoice_journals(
    request: Request,
    invoice_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get journal entries related to a sales invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            # Verify invoice exists and belongs to tenant
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, journal_id, cogs_journal_id
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get journal entries that reference this invoice
            entries = await conn.fetch(
                """
                SELECT
                    je.id,
                    je.journal_number,
                    je.journal_date,
                    je.description,
                    je.source_type,
                    je.status,
                    je.total_debit,
                    je.total_credit,
                    je.created_at,
                    je.reversal_of_id,
                    je.reversed_by_id
                FROM journal_entries je
                WHERE je.tenant_id = $1
                    AND (je.source_id = $2 OR je.id = $3 OR je.id = $4)
                    AND je.status IN ('POSTED', 'VOID')
                ORDER BY je.journal_date DESC, je.created_at DESC
                LIMIT $5 OFFSET $6
                """,
                ctx["tenant_id"],
                invoice_id,
                invoice["journal_id"],
                invoice["cogs_journal_id"],
                limit,
                offset,
            )

            # Get total count
            total = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM journal_entries je
                WHERE je.tenant_id = $1
                    AND (je.source_id = $2 OR je.id = $3 OR je.id = $4)
                    AND je.status IN ('POSTED', 'VOID')
                """,
                ctx["tenant_id"],
                invoice_id,
                invoice["journal_id"],
                invoice["cogs_journal_id"],
            )

            # Build response with lines for each entry
            result_entries = []
            for entry in entries:
                # Get lines for this journal entry
                lines = await conn.fetch(
                    """
                    SELECT
                        jl.line_number,
                        a.account_code,
                        a.name as account_name,
                        jl.memo,
                        jl.debit,
                        jl.credit
                    FROM journal_lines jl
                    INNER JOIN chart_of_accounts a ON a.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    entry["id"],
                )

                result_entries.append(
                    {
                        "id": str(entry["id"]),
                        "journal_number": entry["journal_number"],
                        "date": entry["journal_date"].isoformat(),
                        "description": entry["description"],
                        "source_type": entry["source_type"],
                        "status": entry["status"],
                        "total_debit": float(entry["total_debit"]),
                        "total_credit": float(entry["total_credit"]),
                        "is_reversal": entry["reversal_of_id"] is not None,
                        "is_reversed": entry["reversed_by_id"] is not None,
                        "lines": [
                            {
                                "line_number": line["line_number"],
                                "account_code": line["account_code"],
                                "account_name": line["account_name"],
                                "memo": line["memo"],
                                "debit": float(line["debit"]),
                                "credit": float(line["credit"]),
                            }
                            for line in lines
                        ],
                    }
                )

            return {
                "success": True,
                "data": result_entries,
                "total": total or 0,
                "page": page,
                "limit": limit,
                "has_more": (offset + limit) < (total or 0),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice journals {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")


# =============================================================================
# SALES INVOICE ATTACHMENTS
# =============================================================================


@router.post("/{invoice_id}/attachments", status_code=201)
async def upload_invoice_attachment(
    request: Request,
    invoice_id: UUID,
    file: UploadFile = File(..., description="Image or PDF file (max 5MB)"),
):
    """Upload an attachment to a sales invoice (stored in MinIO)."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")
        await file.seek(0)

        allowed_types = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type {file.content_type} not allowed. Use JPEG, PNG, WebP, or PDF.",
            )

        tenant_id = ctx["tenant_id"]
        user_id = ctx["user_id"]
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = {tenant_id}")

            inv = await conn.fetchrow(
                "SELECT id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                invoice_id,
                tenant_id,
            )
            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found")

            storage = get_storage_service()
            result = await storage.upload_file(
                file=file,
                tenant_id=tenant_id,
                category="invoice-attachments",
            )

            import uuid as uuid_mod

            attachment_id = uuid_mod.uuid4()
            await conn.execute(
                """INSERT INTO sales_invoice_attachments (id, invoice_id, tenant_id, filename, file_path, file_size, mime_type, uploaded_by)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                attachment_id,
                invoice_id,
                tenant_id,
                file.filename,
                result.file_path,
                len(content),
                file.content_type,
                user_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(attachment_id),
                    "filename": file.filename,
                    "url": result.url,
                    "size": len(content),
                    "mime_type": file.content_type,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error uploading attachment for invoice {invoice_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to upload attachment")


@router.get("/{invoice_id}/attachments")
async def list_invoice_attachments(
    request: Request,
    invoice_id: UUID,
):
    """List attachments for a sales invoice."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = {tenant_id}")

        inv = await conn.fetchrow(
            "SELECT id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
            invoice_id,
            tenant_id,
        )
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")

        rows = await conn.fetch(
            "SELECT id, filename, file_path, file_size, mime_type, uploaded_at FROM sales_invoice_attachments WHERE invoice_id = $1 ORDER BY uploaded_at DESC",
            invoice_id,
        )

        storage = get_storage_service()

        attachments = []
        for r in rows:
            try:
                url = (
                    await storage.generate_signed_url(r["file_path"])
                    if r["file_path"]
                    else None
                )
            except Exception:
                url = None
            attachments.append(
                {
                    "id": str(r["id"]),
                    "filename": r["filename"],
                    "url": url,
                    "size": r["file_size"],
                    "mime_type": r["mime_type"],
                    "uploaded_at": r["uploaded_at"].isoformat()
                    if r["uploaded_at"]
                    else None,
                }
            )

        return {"attachments": attachments}


@router.delete("/{invoice_id}/attachments/{attachment_id}")
async def delete_invoice_attachment(
    request: Request,
    invoice_id: UUID,
    attachment_id: UUID,
):
    """Delete a sales invoice attachment."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = {tenant_id}")

        row = await conn.fetchrow(
            """SELECT sa.id, sa.file_path FROM sales_invoice_attachments sa
               JOIN sales_invoices si ON si.id = sa.invoice_id
               WHERE sa.id = $1 AND sa.invoice_id = $2 AND si.tenant_id = $3""",
            attachment_id,
            invoice_id,
            tenant_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Attachment not found")

        storage = get_storage_service()
        try:
            await storage.delete_file(row["file_path"])
        except Exception:
            pass

        await conn.execute(
            "DELETE FROM sales_invoice_attachments WHERE id = $1", attachment_id
        )

        return {"success": True, "message": "Attachment deleted"}


@router.get("/{invoice_id}/attachments/{attachment_id}/download")
async def download_invoice_attachment(
    request: Request,
    invoice_id: UUID,
    attachment_id: UUID,
):
    """Proxy-download a sales invoice attachment."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = {tenant_id}")
        row = await conn.fetchrow(
            """SELECT sa.filename, sa.file_path, sa.mime_type
            FROM sales_invoice_attachments sa
            JOIN sales_invoices si ON si.id = sa.invoice_id
            WHERE sa.id = $1 AND sa.invoice_id = $2 AND si.tenant_id = $3""",
            attachment_id,
            invoice_id,
            tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    storage = get_storage_service()
    try:
        obj = storage.client.get_object(
            Bucket=storage.config.bucket, Key=row["file_path"]
        )
        body = obj["Body"]

        def iter_body():
            while chunk := body.read(65536):
                yield chunk
            body.close()

        return StreamingResponse(
            iter_body(),
            media_type=row["mime_type"] or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{row["filename"]}"',
                "Cache-Control": "private, max-age=3600",
            },
        )
    except Exception as e:
        logger.error(
            f"Error downloading attachment {attachment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to download attachment")
