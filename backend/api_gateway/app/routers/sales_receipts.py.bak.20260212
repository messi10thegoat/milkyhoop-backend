"""
Sales Receipts Router
=====================
POS/Cash sales endpoints with immediate payment.
Creates TWO journal entries: Sales + COGS.
"""
from datetime import date, time
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.sales_receipts import (
    CreateSalesReceiptRequest,
    CreateSalesReceiptResponse,
    DailySalesSummary,
    DailySummaryResponse,
    SalesByPaymentMethodResponse,
    SalesByWarehouseResponse,
    SalesReceiptData,
    SalesReceiptDetailData,
    SalesReceiptDetailResponse,
    SalesReceiptItemData,
    SalesReceiptListResponse,
    VoidSalesReceiptRequest,
    VoidSalesReceiptResponse,
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
# HELPER: Calculate line totals
# ============================================================================

def calculate_line_totals(item: dict) -> dict:
    quantity = Decimal(str(item["quantity"]))
    unit_price = item["unit_price"]
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

    subtotal = int(quantity * unit_price)
    discount_amount = int(subtotal * discount_percent / 100)
    after_discount = subtotal - discount_amount
    tax_amount = int(after_discount * tax_rate / 100)
    line_total = after_discount + tax_amount

    return {
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_amount": tax_amount,
        "line_total": line_total,
    }


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("", response_model=SalesReceiptListResponse)
async def list_sales_receipts(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = None,
    payment_method: Optional[str] = None,
    warehouse_id: Optional[UUID] = None,
    customer_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    """List sales receipts"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["sr.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"sr.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if payment_method:
            where_clauses.append(f"sr.payment_method = ${param_idx}")
            params.append(payment_method)
            param_idx += 1

        if warehouse_id:
            where_clauses.append(f"sr.warehouse_id = ${param_idx}")
            params.append(warehouse_id)
            param_idx += 1

        if customer_id:
            where_clauses.append(f"sr.customer_id = ${param_idx}")
            params.append(customer_id)
            param_idx += 1

        if date_from:
            where_clauses.append(f"sr.receipt_date >= ${param_idx}")
            params.append(date_from)
            param_idx += 1

        if date_to:
            where_clauses.append(f"sr.receipt_date <= ${param_idx}")
            params.append(date_to)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(f"SELECT COUNT(*) FROM sales_receipts sr WHERE {where_sql}", *params)

        rows = await conn.fetch(
            f"""
            SELECT sr.*, w.name as warehouse_name
            FROM sales_receipts sr
            LEFT JOIN warehouses w ON sr.warehouse_id = w.id
            WHERE {where_sql}
            ORDER BY sr.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = [SalesReceiptData(**dict(row)) for row in rows]
        return SalesReceiptListResponse(data=data, total=total, has_more=(skip + limit) < total)


@router.get("/daily-summary", response_model=DailySummaryResponse)
async def get_daily_summary(
    request: Request,
    summary_date: date = Query(default=None),
    warehouse_id: Optional[UUID] = None,
):
    """Get daily sales summary"""
    ctx = get_user_context(request)
    pool = await get_pool()

    if summary_date is None:
        summary_date = date.today()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            "SELECT * FROM get_daily_sales_summary($1, $2, $3)",
            ctx["tenant_id"], summary_date, warehouse_id
        )

        return DailySummaryResponse(
            data=DailySalesSummary(date=summary_date, **dict(row))
        )


@router.get("/{receipt_id}", response_model=SalesReceiptDetailResponse)
async def get_sales_receipt(request: Request, receipt_id: UUID):
    """Get sales receipt details with items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            SELECT sr.*, w.name as warehouse_name
            FROM sales_receipts sr
            LEFT JOIN warehouses w ON sr.warehouse_id = w.id
            WHERE sr.id = $1 AND sr.tenant_id = $2
            """,
            receipt_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Sales receipt not found")

        items = await conn.fetch(
            "SELECT * FROM sales_receipt_items WHERE sales_receipt_id = $1 ORDER BY line_number",
            receipt_id
        )

        data = SalesReceiptDetailData(
            **dict(row),
            items=[SalesReceiptItemData(**dict(item)) for item in items]
        )

        return SalesReceiptDetailResponse(data=data)


@router.post("", response_model=CreateSalesReceiptResponse)
async def create_sales_receipt(request: Request, body: CreateSalesReceiptRequest):
    """Create sales receipt - atomic operation with journal entries"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            # Generate receipt number
            receipt_number = await conn.fetchval(
                "SELECT generate_sales_receipt_number($1)",
                ctx["tenant_id"]
            )

            # Get default warehouse if not provided
            warehouse_id = body.warehouse_id
            if not warehouse_id:
                warehouse_id = await conn.fetchval(
                    "SELECT get_default_warehouse($1)",
                    ctx["tenant_id"]
                )

            # Calculate totals
            subtotal = 0
            tax_amount = 0
            total_cost = 0
            processed_items = []

            for idx, item in enumerate(body.items, 1):
                line_calc = calculate_line_totals(item.model_dump())

                # Get item cost
                item_row = await conn.fetchrow(
                    "SELECT unit_cost FROM items WHERE id = $1",
                    item.item_id
                )
                unit_cost = item_row["unit_cost"] if item_row else 0
                item_total_cost = int(item.quantity * unit_cost)

                processed_items.append({
                    **item.model_dump(),
                    **line_calc,
                    "unit_cost": unit_cost,
                    "total_cost": item_total_cost,
                    "line_number": idx,
                })

                subtotal += line_calc["subtotal"]
                tax_amount += line_calc["tax_amount"]
                total_cost += item_total_cost

            # Apply header discount
            discount_amount = body.discount_amount
            if body.discount_percent > 0:
                discount_amount = int(subtotal * body.discount_percent / 100)

            total_amount = subtotal - discount_amount + tax_amount
            change_amount = body.amount_received - total_amount

            if change_amount < 0:
                raise HTTPException(status_code=400, detail="Amount received is less than total")

            # Create receipt header
            receipt_row = await conn.fetchrow(
                """
                INSERT INTO sales_receipts (
                    tenant_id, receipt_number, receipt_date, receipt_time,
                    customer_id, customer_name, customer_phone, customer_email,
                    warehouse_id, subtotal, discount_percent, discount_amount,
                    tax_amount, total_amount, payment_method, payment_reference,
                    amount_received, change_amount, bank_account_id,
                    pos_terminal, shift_number, notes, internal_notes,
                    status, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, 'completed', $24
                )
                RETURNING *
                """,
                ctx["tenant_id"], receipt_number, body.receipt_date, body.receipt_time,
                body.customer_id, body.customer_name, body.customer_phone, body.customer_email,
                warehouse_id, subtotal, body.discount_percent, discount_amount,
                tax_amount, total_amount, body.payment_method, body.payment_reference,
                body.amount_received, change_amount, body.bank_account_id,
                body.pos_terminal, body.shift_number, body.notes, body.internal_notes,
                ctx.get("user_id")
            )

            receipt_id = receipt_row["id"]

            # Create items
            items_data = []
            for item in processed_items:
                item_row = await conn.fetchrow(
                    """
                    INSERT INTO sales_receipt_items (
                        sales_receipt_id, item_id, item_code, item_name, description,
                        quantity, unit, unit_price, discount_percent, discount_amount,
                        tax_id, tax_rate, tax_amount, subtotal, line_total,
                        unit_cost, total_cost, batch_id, batch_number, serial_ids, line_number
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21
                    )
                    RETURNING *
                    """,
                    receipt_id, item["item_id"], item.get("item_code"), item["item_name"],
                    item.get("description"), item["quantity"], item.get("unit"),
                    item["unit_price"], item.get("discount_percent", 0), item["discount_amount"],
                    item.get("tax_id"), item.get("tax_rate", 0), item["tax_amount"],
                    item["subtotal"], item["line_total"], item["unit_cost"], item["total_cost"],
                    item.get("batch_id"), item.get("batch_number"), item.get("serial_ids"),
                    item["line_number"]
                )
                items_data.append(SalesReceiptItemData(**dict(item_row)))

                # Update inventory (reduce stock)
                if warehouse_id:
                    await conn.execute(
                        """
                        INSERT INTO inventory_ledger (
                            tenant_id, item_id, warehouse_id, quantity_change,
                            unit_cost, total_value, source_type, source_id, transaction_date
                        ) VALUES ($1, $2, $3, $4, $5, $6, 'SALES_RECEIPT', $7, $8)
                        """,
                        ctx["tenant_id"], item["item_id"], warehouse_id,
                        -item["quantity"], item["unit_cost"], -item["total_cost"],
                        receipt_id, body.receipt_date
                    )

            # Create journal entries
            # 1. Sales Journal
            journal_id = uuid4()
            journal_number = f"SR-JE-{receipt_number}"

            # Determine cash/bank account
            if body.payment_method == "cash":
                cash_account = "1-10100"  # Kas
            else:
                cash_account = "1-10200"  # Bank

            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'SALES_RECEIPT', $6, 'POSTED', $7, $7, $8)
                """,
                journal_id, ctx["tenant_id"], journal_number, body.receipt_date,
                f"Sales Receipt {receipt_number}", receipt_id, total_amount, ctx.get("user_id")
            )

            # Journal lines
            # DR Cash/Bank
            cash_acct = await conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                ctx["tenant_id"], cash_account
            )
            if cash_acct:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                    VALUES ($1, $2, $3, 1, $4, 0, $5)
                    """,
                    uuid4(), journal_id, cash_acct, total_amount, f"Receipt {receipt_number}"
                )

            # CR Sales
            sales_acct = await conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '4-10100'",
                ctx["tenant_id"]
            )
            if sales_acct:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                    VALUES ($1, $2, $3, 2, 0, $4, $5)
                    """,
                    uuid4(), journal_id, sales_acct, subtotal - discount_amount, "Sales"
                )

            # CR Tax (if any)
            if tax_amount > 0:
                tax_acct = await conn.fetchval(
                    "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '2-10300'",
                    ctx["tenant_id"]
                )
                if tax_acct:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                        VALUES ($1, $2, $3, 3, 0, $4, $5)
                        """,
                        uuid4(), journal_id, tax_acct, tax_amount, "PPN Keluaran"
                    )

            # 2. COGS Journal (if has cost)
            cogs_journal_id = None
            if total_cost > 0:
                cogs_journal_id = uuid4()
                cogs_journal_number = f"SR-COGS-{receipt_number}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'SALES_RECEIPT_COGS', $6, 'POSTED', $7, $7, $8)
                    """,
                    cogs_journal_id, ctx["tenant_id"], cogs_journal_number, body.receipt_date,
                    f"COGS for {receipt_number}", receipt_id, total_cost, ctx.get("user_id")
                )

                # DR HPP
                hpp_acct = await conn.fetchval(
                    "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '5-10100'",
                    ctx["tenant_id"]
                )
                if hpp_acct:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                        VALUES ($1, $2, $3, 1, $4, 0, $5)
                        """,
                        uuid4(), cogs_journal_id, hpp_acct, total_cost, "Cost of Goods Sold"
                    )

                # CR Inventory
                inv_acct = await conn.fetchval(
                    "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10400'",
                    ctx["tenant_id"]
                )
                if inv_acct:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                        VALUES ($1, $2, $3, 2, 0, $4, $5)
                        """,
                        uuid4(), cogs_journal_id, inv_acct, total_cost, "Inventory reduction"
                    )

            # Update receipt with journal IDs
            await conn.execute(
                "UPDATE sales_receipts SET journal_id = $1, cogs_journal_id = $2 WHERE id = $3",
                journal_id, cogs_journal_id, receipt_id
            )

            # Fetch final receipt
            final_row = await conn.fetchrow(
                """
                SELECT sr.*, w.name as warehouse_name
                FROM sales_receipts sr
                LEFT JOIN warehouses w ON sr.warehouse_id = w.id
                WHERE sr.id = $1
                """,
                receipt_id
            )

            data = SalesReceiptDetailData(**dict(final_row), items=items_data)

            return CreateSalesReceiptResponse(
                data=data,
                journal_id=journal_id,
                cogs_journal_id=cogs_journal_id
            )


@router.post("/{receipt_id}/void", response_model=VoidSalesReceiptResponse)
async def void_sales_receipt(request: Request, receipt_id: UUID, body: VoidSalesReceiptRequest):
    """Void a sales receipt - creates reversing journal entries"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

            existing = await conn.fetchrow(
                "SELECT * FROM sales_receipts WHERE id = $1 AND tenant_id = $2",
                receipt_id, ctx["tenant_id"]
            )

            if not existing:
                raise HTTPException(status_code=404, detail="Sales receipt not found")

            if existing["status"] == "void":
                raise HTTPException(status_code=400, detail="Receipt already voided")

            # Void the original journals (create reversing entries)
            if existing["journal_id"]:
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET status = 'VOID', voided_by = $2, void_reason = $3, updated_at = NOW()
                    WHERE id = $1
                    """,
                    existing["journal_id"], ctx.get("user_id"), body.reason
                )

            if existing["cogs_journal_id"]:
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET status = 'VOID', voided_by = $2, void_reason = $3, updated_at = NOW()
                    WHERE id = $1
                    """,
                    existing["cogs_journal_id"], ctx.get("user_id"), body.reason
                )

            # Reverse inventory
            items = await conn.fetch(
                "SELECT * FROM sales_receipt_items WHERE sales_receipt_id = $1",
                receipt_id
            )

            for item in items:
                if existing["warehouse_id"]:
                    await conn.execute(
                        """
                        INSERT INTO inventory_ledger (
                            tenant_id, item_id, warehouse_id, quantity_change,
                            unit_cost, total_value, source_type, source_id, transaction_date
                        ) VALUES ($1, $2, $3, $4, $5, $6, 'SALES_RECEIPT_VOID', $7, CURRENT_DATE)
                        """,
                        ctx["tenant_id"], item["item_id"], existing["warehouse_id"],
                        item["quantity"], item["unit_cost"], item["total_cost"], receipt_id
                    )

            # Update receipt status
            row = await conn.fetchrow(
                """
                UPDATE sales_receipts
                SET status = 'void', voided_at = NOW(), voided_by = $2, void_reason = $3, updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                receipt_id, ctx.get("user_id"), body.reason
            )

            return VoidSalesReceiptResponse(data=SalesReceiptData(**dict(row)))
