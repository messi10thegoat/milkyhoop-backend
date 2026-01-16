"""
Recurring Invoices Router
=========================
Template-based recurring invoice management.
"""
from datetime import date, timedelta
from typing import Optional
from uuid import UUID

import asyncpg
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.recurring_invoices import (
    CreateRecurringInvoiceRequest,
    CreateRecurringInvoiceResponse,
    DueRecurringInvoicesResponse,
    GenerateInvoiceResponse,
    GeneratedInvoice,
    PauseRecurringInvoiceRequest,
    PauseRecurringInvoiceResponse,
    ProcessDueResponse,
    ProcessDueResult,
    RecurringInvoiceData,
    RecurringInvoiceDetailData,
    RecurringInvoiceDetailResponse,
    RecurringInvoiceHistoryResponse,
    RecurringInvoiceItemData,
    RecurringInvoiceListResponse,
    ResumeRecurringInvoiceResponse,
    UpdateRecurringInvoiceRequest,
    UpdateRecurringInvoiceResponse,
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


def calculate_next_date(current: date, frequency: str, interval: int = 1,
                        day_of_month: int = None, day_of_week: int = None) -> date:
    """Calculate next invoice date based on frequency"""
    if frequency == "daily":
        return current + timedelta(days=interval)
    elif frequency == "weekly":
        next_date = current + timedelta(weeks=interval)
        if day_of_week is not None:
            diff = day_of_week - next_date.weekday()
            if diff <= 0:
                diff += 7
            next_date = next_date + timedelta(days=diff)
        return next_date
    elif frequency == "monthly":
        next_date = current + relativedelta(months=interval)
        if day_of_month:
            next_date = next_date.replace(day=min(day_of_month, 28))
        return next_date
    elif frequency == "quarterly":
        next_date = current + relativedelta(months=interval * 3)
        if day_of_month:
            next_date = next_date.replace(day=min(day_of_month, 28))
        return next_date
    elif frequency == "yearly":
        next_date = current + relativedelta(years=interval)
        if day_of_month:
            next_date = next_date.replace(day=min(day_of_month, 28))
        return next_date
    return current


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("", response_model=RecurringInvoiceListResponse)
async def list_recurring_invoices(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = None,
    customer_id: Optional[UUID] = None,
):
    """List recurring invoice templates"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["ri.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"ri.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if customer_id:
            where_clauses.append(f"ri.customer_id = ${param_idx}")
            params.append(customer_id)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM recurring_invoices ri WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT ri.*, w.name as warehouse_name
            FROM recurring_invoices ri
            LEFT JOIN warehouses w ON ri.warehouse_id = w.id
            WHERE {where_sql}
            ORDER BY ri.next_invoice_date ASC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = [RecurringInvoiceData(**dict(row)) for row in rows]
        return RecurringInvoiceListResponse(data=data, total=total, has_more=(skip + limit) < total)


@router.get("/due", response_model=DueRecurringInvoicesResponse)
async def get_due_recurring_invoices(request: Request, as_of_date: date = None):
    """Get recurring invoices due for generation"""
    ctx = get_user_context(request)
    pool = await get_pool()

    if as_of_date is None:
        as_of_date = date.today()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_due_recurring_invoices($1, $2)",
            ctx["tenant_id"], as_of_date
        )

        return DueRecurringInvoicesResponse(data=[dict(row) for row in rows], total=len(rows))


@router.get("/{recurring_id}", response_model=RecurringInvoiceDetailResponse)
async def get_recurring_invoice(request: Request, recurring_id: UUID):
    """Get recurring invoice details with items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            SELECT ri.*, w.name as warehouse_name
            FROM recurring_invoices ri
            LEFT JOIN warehouses w ON ri.warehouse_id = w.id
            WHERE ri.id = $1 AND ri.tenant_id = $2
            """,
            recurring_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Recurring invoice not found")

        items = await conn.fetch(
            "SELECT * FROM recurring_invoice_items WHERE recurring_invoice_id = $1 ORDER BY line_number",
            recurring_id
        )

        data = RecurringInvoiceDetailData(
            **dict(row),
            items=[RecurringInvoiceItemData(**dict(item)) for item in items]
        )

        return RecurringInvoiceDetailResponse(data=data)


@router.post("", response_model=CreateRecurringInvoiceResponse)
async def create_recurring_invoice(request: Request, body: CreateRecurringInvoiceRequest):
    """Create a new recurring invoice template"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Verify customer
            customer = await conn.fetchrow(
                "SELECT id, name FROM customers WHERE id = $1 AND tenant_id = $2",
                body.customer_id, ctx["tenant_id"]
            )
            if not customer:
                raise HTTPException(status_code=400, detail="Customer not found")

            customer_name = body.customer_name or customer["name"]

            # Calculate initial next_invoice_date
            next_invoice_date = body.start_date

            # Calculate totals
            subtotal = 0
            tax_amount = 0
            items_data = []

            for idx, item in enumerate(body.items, 1):
                item_subtotal = int(item.quantity * item.unit_price)
                item_discount = int(item_subtotal * item.discount_percent / 100)
                after_discount = item_subtotal - item_discount
                item_tax = int(after_discount * item.tax_rate / 100)
                item_total = after_discount + item_tax

                items_data.append({
                    **item.model_dump(),
                    "subtotal": item_subtotal,
                    "discount_amount": item_discount,
                    "tax_amount": item_tax,
                    "line_total": item_total,
                    "line_number": idx,
                })

                subtotal += item_subtotal
                tax_amount += item_tax

            discount_amount = body.discount_amount
            if body.discount_percent > 0:
                discount_amount = int(subtotal * body.discount_percent / 100)

            total_amount = subtotal - discount_amount + tax_amount

            # Create header
            row = await conn.fetchrow(
                """
                INSERT INTO recurring_invoices (
                    tenant_id, template_name, template_code, customer_id, customer_name,
                    warehouse_id, frequency, interval_count, day_of_month, day_of_week,
                    start_date, end_date, next_invoice_date, due_days, payment_terms,
                    subtotal, discount_percent, discount_amount, tax_amount, total_amount,
                    auto_send, auto_post, invoice_notes, internal_notes, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                    $16, $17, $18, $19, $20, $21, $22, $23, $24, $25
                )
                RETURNING *
                """,
                ctx["tenant_id"], body.template_name, body.template_code,
                body.customer_id, customer_name, body.warehouse_id,
                body.frequency, body.interval_count, body.day_of_month, body.day_of_week,
                body.start_date, body.end_date, next_invoice_date, body.due_days, body.payment_terms,
                subtotal, body.discount_percent, discount_amount, tax_amount, total_amount,
                body.auto_send, body.auto_post, body.invoice_notes, body.internal_notes,
                ctx.get("user_id")
            )

            recurring_id = row["id"]

            # Create items
            final_items = []
            for item in items_data:
                item_row = await conn.fetchrow(
                    """
                    INSERT INTO recurring_invoice_items (
                        recurring_invoice_id, item_id, item_code, item_name, description,
                        quantity, unit, unit_price, discount_percent, discount_amount,
                        tax_id, tax_rate, tax_amount, subtotal, line_total, line_number
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    RETURNING *
                    """,
                    recurring_id, item.get("item_id"), item.get("item_code"), item.get("item_name"),
                    item["description"], item["quantity"], item.get("unit"), item["unit_price"],
                    item.get("discount_percent", 0), item["discount_amount"],
                    item.get("tax_id"), item.get("tax_rate", 0), item["tax_amount"],
                    item["subtotal"], item["line_total"], item["line_number"]
                )
                final_items.append(RecurringInvoiceItemData(**dict(item_row)))

            data = RecurringInvoiceDetailData(**dict(row), items=final_items)

            return CreateRecurringInvoiceResponse(data=data)


@router.post("/{recurring_id}/pause", response_model=PauseRecurringInvoiceResponse)
async def pause_recurring_invoice(request: Request, recurring_id: UUID, body: PauseRecurringInvoiceRequest = None):
    """Pause a recurring invoice"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM recurring_invoices WHERE id = $1 AND tenant_id = $2",
            recurring_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Recurring invoice not found")

        if existing["status"] != "active":
            raise HTTPException(status_code=400, detail="Can only pause active recurring invoices")

        reason = body.reason if body else None

        row = await conn.fetchrow(
            """
            UPDATE recurring_invoices
            SET status = 'paused', paused_at = NOW(), paused_by = $2, pause_reason = $3, updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            recurring_id, ctx.get("user_id"), reason
        )

        return PauseRecurringInvoiceResponse(data=RecurringInvoiceData(**dict(row)))


@router.post("/{recurring_id}/resume", response_model=ResumeRecurringInvoiceResponse)
async def resume_recurring_invoice(request: Request, recurring_id: UUID):
    """Resume a paused recurring invoice"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM recurring_invoices WHERE id = $1 AND tenant_id = $2",
            recurring_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Recurring invoice not found")

        if existing["status"] != "paused":
            raise HTTPException(status_code=400, detail="Can only resume paused recurring invoices")

        # Recalculate next_invoice_date if it's in the past
        next_date = existing["next_invoice_date"]
        today = date.today()
        while next_date < today:
            next_date = calculate_next_date(
                next_date,
                existing["frequency"],
                existing["interval_count"],
                existing["day_of_month"],
                existing["day_of_week"]
            )

        row = await conn.fetchrow(
            """
            UPDATE recurring_invoices
            SET status = 'active', paused_at = NULL, paused_by = NULL, pause_reason = NULL,
                next_invoice_date = $2, updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            recurring_id, next_date
        )

        return ResumeRecurringInvoiceResponse(data=RecurringInvoiceData(**dict(row)))


@router.post("/{recurring_id}/generate", response_model=GenerateInvoiceResponse)
async def generate_invoice(request: Request, recurring_id: UUID):
    """Generate an invoice from template now"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            template = await conn.fetchrow(
                "SELECT * FROM recurring_invoices WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                recurring_id, ctx["tenant_id"]
            )

            if not template:
                raise HTTPException(status_code=404, detail="Recurring invoice not found")

            if template["status"] != "active":
                raise HTTPException(status_code=400, detail="Recurring invoice is not active")

            # Generate invoice number
            invoice_number = await conn.fetchval(
                "SELECT generate_invoice_number($1)",
                ctx["tenant_id"]
            )

            invoice_date = template["next_invoice_date"]
            due_date = invoice_date + timedelta(days=template["due_days"])

            # Create sales invoice
            invoice_row = await conn.fetchrow(
                """
                INSERT INTO sales_invoices (
                    tenant_id, invoice_number, invoice_date, due_date,
                    customer_id, customer_name, warehouse_id,
                    subtotal, discount_percent, discount_amount, tax_amount, total_amount,
                    notes, recurring_invoice_id, is_recurring, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, true, 'draft', $15)
                RETURNING id, invoice_number
                """,
                ctx["tenant_id"], invoice_number, invoice_date, due_date,
                template["customer_id"], template["customer_name"], template["warehouse_id"],
                template["subtotal"], template["discount_percent"], template["discount_amount"],
                template["tax_amount"], template["total_amount"], template["invoice_notes"],
                recurring_id, ctx.get("user_id")
            )

            invoice_id = invoice_row["id"]

            # Copy items
            template_items = await conn.fetch(
                "SELECT * FROM recurring_invoice_items WHERE recurring_invoice_id = $1",
                recurring_id
            )

            for item in template_items:
                await conn.execute(
                    """
                    INSERT INTO sales_invoice_items (
                        sales_invoice_id, item_id, item_code, item_name, description,
                        quantity, unit, unit_price, discount_percent, discount_amount,
                        tax_id, tax_rate, tax_amount, subtotal, line_total, line_number
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    """,
                    invoice_id, item["item_id"], item["item_code"], item["item_name"],
                    item["description"], item["quantity"], item["unit"], item["unit_price"],
                    item["discount_percent"], item["discount_amount"], item["tax_id"],
                    item["tax_rate"], item["tax_amount"], item["subtotal"], item["line_total"],
                    item["line_number"]
                )

            # Calculate next invoice date
            next_date = calculate_next_date(
                invoice_date,
                template["frequency"],
                template["interval_count"],
                template["day_of_month"],
                template["day_of_week"]
            )

            # Check if should complete
            new_status = "active"
            if template["end_date"] and next_date > template["end_date"]:
                new_status = "completed"

            # Update template
            await conn.execute(
                """
                UPDATE recurring_invoices
                SET next_invoice_date = $2, last_invoice_date = $3,
                    invoices_generated = invoices_generated + 1,
                    total_invoiced = total_invoiced + $4,
                    status = $5, updated_at = NOW()
                WHERE id = $1
                """,
                recurring_id, next_date, invoice_date, template["total_amount"], new_status
            )

            return GenerateInvoiceResponse(
                invoice_id=invoice_id,
                invoice_number=invoice_number,
                next_invoice_date=next_date
            )


@router.post("/process-due", response_model=ProcessDueResponse)
async def process_due_invoices(request: Request):
    """Process all due recurring invoices (cron endpoint)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    results = []
    succeeded = 0
    failed = 0

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        due_invoices = await conn.fetch(
            "SELECT * FROM get_due_recurring_invoices($1, $2)",
            ctx["tenant_id"], date.today()
        )

        for due in due_invoices:
            try:
                # Generate each invoice
                result = await generate_invoice(request, due["id"])
                results.append(ProcessDueResult(
                    recurring_invoice_id=due["id"],
                    template_name=due["template_name"],
                    success=True,
                    invoice_id=result.invoice_id,
                    invoice_number=result.invoice_number
                ))
                succeeded += 1
            except Exception as e:
                results.append(ProcessDueResult(
                    recurring_invoice_id=due["id"],
                    template_name=due["template_name"],
                    success=False,
                    error=str(e)
                ))
                failed += 1

    return ProcessDueResponse(
        processed=len(results),
        succeeded=succeeded,
        failed=failed,
        results=results
    )


@router.get("/{recurring_id}/history", response_model=RecurringInvoiceHistoryResponse)
async def get_recurring_invoice_history(
    request: Request,
    recurring_id: UUID,
    limit: int = Query(50, ge=1, le=100),
):
    """Get generated invoices history"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        template = await conn.fetchrow(
            "SELECT id, template_name FROM recurring_invoices WHERE id = $1 AND tenant_id = $2",
            recurring_id, ctx["tenant_id"]
        )

        if not template:
            raise HTTPException(status_code=404, detail="Recurring invoice not found")

        rows = await conn.fetch(
            """
            SELECT id as invoice_id, invoice_number, invoice_date, due_date, total_amount, status
            FROM sales_invoices
            WHERE recurring_invoice_id = $1
            ORDER BY invoice_date DESC
            LIMIT $2
            """,
            recurring_id, limit
        )

        return RecurringInvoiceHistoryResponse(
            recurring_invoice_id=recurring_id,
            template_name=template["template_name"],
            data=[GeneratedInvoice(**dict(row)) for row in rows],
            total=len(rows)
        )
