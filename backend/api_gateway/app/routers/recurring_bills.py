"""
Recurring Bills Router
======================
Auto-generate bills on schedule (mirror of recurring invoices).
"""
from datetime import date
from typing import Optional, List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from dateutil.relativedelta import relativedelta

from ..config import settings
from ..schemas.recurring_bills import (
    RecurringBillCreate,
    RecurringBillUpdate,
    RecurringBillResponse,
    RecurringBillDetailResponse,
    RecurringBillListResponse,
    RecurringBillItemResponse,
    DueRecurringBillItem,
    DueRecurringBillsResponse,
    GeneratedBillItem,
    GeneratedBillsResponse,
    GenerateBillRequest,
    GenerateBillResponse,
    ProcessDueBillsRequest,
    ProcessDueBillsResult,
    ProcessDueBillsResponse,
    RecurringBillStats,
    RecurringBillStatus,
    RecurringFrequency,
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


def calculate_next_date(current_date: date, frequency: str, interval: int = 1) -> date:
    """Calculate next bill date based on frequency"""
    if frequency == "daily":
        return current_date + relativedelta(days=interval)
    elif frequency == "weekly":
        return current_date + relativedelta(weeks=interval)
    elif frequency == "monthly":
        return current_date + relativedelta(months=interval)
    elif frequency == "quarterly":
        return current_date + relativedelta(months=interval * 3)
    elif frequency == "yearly":
        return current_date + relativedelta(years=interval)
    return current_date + relativedelta(months=interval)


# ============================================================================
# RECURRING BILL CRUD
# ============================================================================

@router.get("", response_model=RecurringBillListResponse)
async def list_recurring_bills(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[RecurringBillStatus] = None,
    vendor_id: Optional[UUID] = None,
):
    """List recurring bills"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["rb.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"rb.status = ${param_idx}")
            params.append(status.value)
            param_idx += 1

        if vendor_id:
            where_clauses.append(f"rb.vendor_id = ${param_idx}")
            params.append(vendor_id)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM recurring_bills rb WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT rb.*, v.name as vendor_name, v.code as vendor_code
            FROM recurring_bills rb
            JOIN vendors v ON rb.vendor_id = v.id
            WHERE {where_sql}
            ORDER BY rb.next_bill_date ASC
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params, skip, limit
        )

        items = [RecurringBillResponse(**dict(row)) for row in rows]
        return RecurringBillListResponse(items=items, total=total)


@router.get("/due", response_model=DueRecurringBillsResponse)
async def get_due_recurring_bills(
    request: Request,
    as_of_date: Optional[date] = None,
):
    """Get recurring bills due for generation"""
    ctx = get_user_context(request)
    pool = await get_pool()
    check_date = as_of_date or date.today()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_due_recurring_bills($1, $2)",
            ctx["tenant_id"], check_date
        )

        items = [DueRecurringBillItem(
            id=row["id"],
            template_name=row["template_name"],
            vendor_id=row["vendor_id"],
            vendor_name=row["vendor_name"],
            next_bill_date=row["next_bill_date"],
            frequency=row["frequency"],
            total_amount=row["total_amount"],
            auto_post=row["auto_post"],
        ) for row in rows]

        total_amount = sum(i.total_amount for i in items)

        return DueRecurringBillsResponse(
            as_of_date=check_date,
            items=items,
            total_amount=total_amount,
        )


@router.get("/stats", response_model=RecurringBillStats)
async def get_recurring_bill_stats(request: Request):
    """Get recurring bill statistics"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            "SELECT * FROM get_recurring_bill_stats($1)",
            ctx["tenant_id"]
        )

        return RecurringBillStats(**dict(row))


@router.get("/{recurring_bill_id}", response_model=RecurringBillDetailResponse)
async def get_recurring_bill(request: Request, recurring_bill_id: UUID):
    """Get recurring bill with items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rb = await conn.fetchrow(
            """
            SELECT rb.*, v.name as vendor_name, v.code as vendor_code
            FROM recurring_bills rb
            JOIN vendors v ON rb.vendor_id = v.id
            WHERE rb.id = $1 AND rb.tenant_id = $2
            """,
            recurring_bill_id, ctx["tenant_id"]
        )
        if not rb:
            raise HTTPException(status_code=404, detail="Recurring bill not found")

        items = await conn.fetch(
            """
            SELECT rbi.*, i.name as item_name, i.code as item_code,
                   coa.account_code, coa.name as account_name,
                   cc.name as cost_center_name, tc.name as tax_name
            FROM recurring_bill_items rbi
            LEFT JOIN items i ON rbi.item_id = i.id
            LEFT JOIN chart_of_accounts coa ON rbi.account_id = coa.id
            LEFT JOIN cost_centers cc ON rbi.cost_center_id = cc.id
            LEFT JOIN tax_codes tc ON rbi.tax_id = tc.id
            WHERE rbi.recurring_bill_id = $1
            ORDER BY rbi.sort_order
            """,
            recurring_bill_id
        )

        return RecurringBillDetailResponse(
            **dict(rb),
            items=[RecurringBillItemResponse(**dict(item)) for item in items]
        )


@router.post("", response_model=RecurringBillResponse, status_code=201)
async def create_recurring_bill(request: Request, data: RecurringBillCreate):
    """Create recurring bill template"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Validate vendor
        vendor = await conn.fetchrow(
            "SELECT id, name, code FROM vendors WHERE id = $1 AND tenant_id = $2",
            data.vendor_id, ctx["tenant_id"]
        )
        if not vendor:
            raise HTTPException(status_code=400, detail="Vendor not found")

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO recurring_bills (
                    tenant_id, template_name, vendor_id, frequency, interval_count,
                    start_date, end_date, next_bill_date, due_days,
                    subtotal, discount_amount, tax_amount, total_amount,
                    auto_post, notes, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                RETURNING *
                """,
                ctx["tenant_id"], data.template_name, data.vendor_id,
                data.frequency.value, data.interval_count,
                data.start_date, data.end_date, data.start_date, data.due_days,
                data.subtotal, data.discount_amount, data.tax_amount, data.total_amount,
                data.auto_post, data.notes, ctx.get("user_id")
            )

            # Insert items
            for i, item in enumerate(data.items):
                await conn.execute(
                    """
                    INSERT INTO recurring_bill_items (
                        recurring_bill_id, item_id, description, quantity, unit_price,
                        account_id, cost_center_id, tax_id, tax_amount, line_total, sort_order
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    row["id"], item.item_id, item.description, item.quantity, item.unit_price,
                    item.account_id, item.cost_center_id, item.tax_id, item.tax_amount,
                    item.line_total, i
                )

            return RecurringBillResponse(
                **dict(row),
                vendor_name=vendor["name"],
                vendor_code=vendor["code"]
            )


@router.patch("/{recurring_bill_id}", response_model=RecurringBillResponse)
async def update_recurring_bill(request: Request, recurring_bill_id: UUID, data: RecurringBillUpdate):
    """Update recurring bill"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            """
            SELECT rb.*, v.name as vendor_name, v.code as vendor_code
            FROM recurring_bills rb
            JOIN vendors v ON rb.vendor_id = v.id
            WHERE rb.id = $1 AND rb.tenant_id = $2
            """,
            recurring_bill_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Recurring bill not found")

        update_data = data.model_dump(exclude_unset=True, exclude={"items"})

        async with conn.transaction():
            if update_data:
                set_clauses = []
                params = []
                for i, (key, value) in enumerate(update_data.items(), start=1):
                    if key == "frequency":
                        value = value.value
                    set_clauses.append(f"{key} = ${i}")
                    params.append(value)

                set_clauses.append("updated_at = NOW()")
                params.extend([recurring_bill_id, ctx["tenant_id"]])

                await conn.execute(
                    f"""
                    UPDATE recurring_bills SET {', '.join(set_clauses)}
                    WHERE id = ${len(params) - 1} AND tenant_id = ${len(params)}
                    """,
                    *params
                )

            # Update items if provided
            if data.items is not None:
                await conn.execute(
                    "DELETE FROM recurring_bill_items WHERE recurring_bill_id = $1",
                    recurring_bill_id
                )
                for i, item in enumerate(data.items):
                    await conn.execute(
                        """
                        INSERT INTO recurring_bill_items (
                            recurring_bill_id, item_id, description, quantity, unit_price,
                            account_id, cost_center_id, tax_id, tax_amount, line_total, sort_order
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        """,
                        recurring_bill_id, item.item_id, item.description,
                        item.quantity, item.unit_price, item.account_id,
                        item.cost_center_id, item.tax_id, item.tax_amount,
                        item.line_total, i
                    )

        row = await conn.fetchrow(
            """
            SELECT rb.*, v.name as vendor_name, v.code as vendor_code
            FROM recurring_bills rb
            JOIN vendors v ON rb.vendor_id = v.id
            WHERE rb.id = $1
            """,
            recurring_bill_id
        )

        return RecurringBillResponse(**dict(row))


@router.delete("/{recurring_bill_id}")
async def delete_recurring_bill(request: Request, recurring_bill_id: UUID):
    """Delete recurring bill (only if no bills generated)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM recurring_bills WHERE id = $1 AND tenant_id = $2",
            recurring_bill_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Recurring bill not found")

        if existing["bills_generated"] > 0:
            raise HTTPException(status_code=400, detail="Cannot delete recurring bill with generated bills")

        await conn.execute("DELETE FROM recurring_bills WHERE id = $1", recurring_bill_id)
        return {"message": "Recurring bill deleted"}


# ============================================================================
# STATUS TRANSITIONS
# ============================================================================

@router.post("/{recurring_bill_id}/pause", response_model=RecurringBillResponse)
async def pause_recurring_bill(request: Request, recurring_bill_id: UUID):
    """Pause recurring bill"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            UPDATE recurring_bills SET status = 'paused', updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2 AND status = 'active'
            RETURNING *
            """,
            recurring_bill_id, ctx["tenant_id"]
        )
        if not row:
            raise HTTPException(status_code=404, detail="Recurring bill not found or not active")

        vendor = await conn.fetchrow("SELECT name, code FROM vendors WHERE id = $1", row["vendor_id"])
        return RecurringBillResponse(**dict(row), vendor_name=vendor["name"], vendor_code=vendor["code"])


@router.post("/{recurring_bill_id}/resume", response_model=RecurringBillResponse)
async def resume_recurring_bill(request: Request, recurring_bill_id: UUID):
    """Resume paused recurring bill"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            UPDATE recurring_bills SET status = 'active', updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2 AND status = 'paused'
            RETURNING *
            """,
            recurring_bill_id, ctx["tenant_id"]
        )
        if not row:
            raise HTTPException(status_code=404, detail="Recurring bill not found or not paused")

        vendor = await conn.fetchrow("SELECT name, code FROM vendors WHERE id = $1", row["vendor_id"])
        return RecurringBillResponse(**dict(row), vendor_name=vendor["name"], vendor_code=vendor["code"])


# ============================================================================
# GENERATE BILLS
# ============================================================================

@router.post("/{recurring_bill_id}/generate", response_model=GenerateBillResponse)
async def generate_bill(request: Request, recurring_bill_id: UUID, data: GenerateBillRequest = None):
    """Generate bill from recurring template"""
    ctx = get_user_context(request)
    pool = await get_pool()
    data = data or GenerateBillRequest()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rb = await conn.fetchrow(
            "SELECT * FROM recurring_bills WHERE id = $1 AND tenant_id = $2",
            recurring_bill_id, ctx["tenant_id"]
        )
        if not rb:
            raise HTTPException(status_code=404, detail="Recurring bill not found")

        if rb["status"] != "active":
            raise HTTPException(status_code=400, detail="Recurring bill is not active")

        bill_date = data.bill_date or rb["next_bill_date"]
        due_date = bill_date + relativedelta(days=rb["due_days"])
        should_post = data.post_immediately if data.post_immediately is not None else rb["auto_post"]

        async with conn.transaction():
            # Generate bill number
            seq = await conn.fetchrow(
                """
                INSERT INTO bill_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = bill_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            bill_number = f"BILL-{bill_date.year}-{seq['last_number']:05d}"

            # Create bill
            bill = await conn.fetchrow(
                """
                INSERT INTO bills (
                    tenant_id, bill_number, vendor_id, bill_date, due_date,
                    subtotal, discount_amount, tax_amount, total_amount,
                    status, recurring_bill_id, is_recurring, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true, $12)
                RETURNING *
                """,
                ctx["tenant_id"], bill_number, rb["vendor_id"], bill_date, due_date,
                rb["subtotal"], rb["discount_amount"], rb["tax_amount"], rb["total_amount"],
                "draft", recurring_bill_id, ctx.get("user_id")
            )

            # Copy items
            items = await conn.fetch(
                "SELECT * FROM recurring_bill_items WHERE recurring_bill_id = $1 ORDER BY sort_order",
                recurring_bill_id
            )
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO bill_items (
                        bill_id, item_id, description, quantity, unit_price,
                        account_id, cost_center_id, tax_id, tax_amount, line_total
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    bill["id"], item["item_id"], item["description"],
                    item["quantity"], item["unit_price"], item["account_id"],
                    item["cost_center_id"], item["tax_id"], item["tax_amount"],
                    item["line_total"]
                )

            # Update recurring bill
            next_date = calculate_next_date(bill_date, rb["frequency"], rb["interval_count"])
            new_status = rb["status"]
            if rb["end_date"] and next_date > rb["end_date"]:
                new_status = "completed"

            await conn.execute(
                """
                UPDATE recurring_bills SET
                    next_bill_date = $2,
                    last_bill_date = $3,
                    bills_generated = bills_generated + 1,
                    status = $4,
                    updated_at = NOW()
                WHERE id = $1
                """,
                recurring_bill_id, next_date, bill_date, new_status
            )

            # Post bill if needed (create AP journal)
            bill_status = "draft"
            if should_post:
                # TODO: Call bill posting logic to create AP journal
                bill_status = "posted"
                await conn.execute(
                    "UPDATE bills SET status = 'posted' WHERE id = $1",
                    bill["id"]
                )

            return GenerateBillResponse(
                bill_id=bill["id"],
                bill_number=bill_number,
                bill_date=bill_date,
                due_date=due_date,
                total_amount=rb["total_amount"],
                status=bill_status,
                next_bill_date=next_date,
            )


@router.post("/process-due", response_model=ProcessDueBillsResponse)
async def process_due_bills(request: Request, data: ProcessDueBillsRequest = None):
    """Process all due recurring bills (batch)"""
    ctx = get_user_context(request)
    pool = await get_pool()
    data = data or ProcessDueBillsRequest()
    check_date = data.as_of_date or date.today()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        due_bills = await conn.fetch(
            "SELECT * FROM get_due_recurring_bills($1, $2)",
            ctx["tenant_id"], check_date
        )

        results = []
        successful = 0
        failed = 0

        for rb in due_bills:
            try:
                # Generate bill for each due recurring bill
                # Reuse generate_bill logic inline
                bill_date = rb["next_bill_date"]

                async with conn.transaction():
                    seq = await conn.fetchrow(
                        """
                        INSERT INTO bill_sequences (tenant_id, last_number)
                        VALUES ($1, 1)
                        ON CONFLICT (tenant_id)
                        DO UPDATE SET last_number = bill_sequences.last_number + 1
                        RETURNING last_number
                        """,
                        ctx["tenant_id"]
                    )
                    bill_number = f"BILL-{bill_date.year}-{seq['last_number']:05d}"

                    rb_full = await conn.fetchrow(
                        "SELECT * FROM recurring_bills WHERE id = $1",
                        rb["id"]
                    )

                    due_date = bill_date + relativedelta(days=rb_full["due_days"])

                    bill = await conn.fetchrow(
                        """
                        INSERT INTO bills (
                            tenant_id, bill_number, vendor_id, bill_date, due_date,
                            subtotal, discount_amount, tax_amount, total_amount,
                            status, recurring_bill_id, is_recurring, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true, $12)
                        RETURNING id
                        """,
                        ctx["tenant_id"], bill_number, rb_full["vendor_id"], bill_date, due_date,
                        rb_full["subtotal"], rb_full["discount_amount"], rb_full["tax_amount"],
                        rb_full["total_amount"], "draft", rb["id"], ctx.get("user_id")
                    )

                    # Copy items
                    await conn.execute(
                        """
                        INSERT INTO bill_items (
                            bill_id, item_id, description, quantity, unit_price,
                            account_id, cost_center_id, tax_id, tax_amount, line_total
                        )
                        SELECT $1, item_id, description, quantity, unit_price,
                            account_id, cost_center_id, tax_id, tax_amount, line_total
                        FROM recurring_bill_items WHERE recurring_bill_id = $2
                        """,
                        bill["id"], rb["id"]
                    )

                    # Update recurring bill
                    next_date = calculate_next_date(
                        bill_date, rb_full["frequency"], rb_full["interval_count"]
                    )
                    new_status = "active"
                    if rb_full["end_date"] and next_date > rb_full["end_date"]:
                        new_status = "completed"

                    await conn.execute(
                        """
                        UPDATE recurring_bills SET
                            next_bill_date = $2, last_bill_date = $3,
                            bills_generated = bills_generated + 1, status = $4, updated_at = NOW()
                        WHERE id = $1
                        """,
                        rb["id"], next_date, bill_date, new_status
                    )

                results.append(ProcessDueBillsResult(
                    recurring_bill_id=rb["id"],
                    template_name=rb["template_name"],
                    bill_id=bill["id"],
                    bill_number=bill_number,
                    success=True,
                ))
                successful += 1

            except Exception as e:
                results.append(ProcessDueBillsResult(
                    recurring_bill_id=rb["id"],
                    template_name=rb["template_name"],
                    success=False,
                    error=str(e),
                ))
                failed += 1

        return ProcessDueBillsResponse(
            processed=len(due_bills),
            successful=successful,
            failed=failed,
            results=results,
        )


@router.get("/{recurring_bill_id}/history", response_model=GeneratedBillsResponse)
async def get_recurring_bill_history(request: Request, recurring_bill_id: UUID):
    """Get bills generated from this template"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rb = await conn.fetchrow(
            """
            SELECT rb.*, v.name as vendor_name, v.code as vendor_code
            FROM recurring_bills rb
            JOIN vendors v ON rb.vendor_id = v.id
            WHERE rb.id = $1 AND rb.tenant_id = $2
            """,
            recurring_bill_id, ctx["tenant_id"]
        )
        if not rb:
            raise HTTPException(status_code=404, detail="Recurring bill not found")

        rows = await conn.fetch(
            "SELECT * FROM get_recurring_bill_history($1)",
            recurring_bill_id
        )

        bills = [GeneratedBillItem(
            bill_id=row["bill_id"],
            bill_number=row["bill_number"],
            bill_date=row["bill_date"],
            due_date=row["due_date"],
            total_amount=row["total_amount"],
            status=row["status"],
            paid_amount=row["paid_amount"],
        ) for row in rows]

        return GeneratedBillsResponse(
            recurring_bill=RecurringBillResponse(**dict(rb)),
            bills=bills,
            total_generated=len(bills),
            total_amount=sum(b.total_amount for b in bills),
        )
