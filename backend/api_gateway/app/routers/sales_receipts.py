"""
Sales Receipts Router
=====================
POS/Cash sales endpoints with immediate payment.
Creates TWO journal entries: Sales + COGS.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas.sales_receipts import (
    CreateSalesReceiptRequest,
    CreateSalesReceiptResponse,
    DailySalesSummary,
    DailySummaryResponse,
    SalesReceiptData,
    SalesReceiptDetailData,
    SalesReceiptDetailResponse,
    SalesReceiptItemData,
    SalesReceiptListResponse,
    VoidSalesReceiptRequest,
    VoidSalesReceiptResponse,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)
from ..services.role_precondition import assert_required_roles_for_path

# Fase C1.2: required role mappings for sales_receipts posting path
# (cash sale, NOT AR settlement — no AR_TRADE).
#
# BANK_OPERATIONAL is intentionally EXCLUDED from the precondition list:
# bank posting uses the user-picked bank_account_id (CoA linked via
# bank_accounts.coa_id). The role only acts as a last-resort fallback
# when payment_method != cash AND no bank_account_id is provided. In
# that fallback path, if the role is unmapped we raise 422 with a clear
# message demanding bank_account_id selection — never silent-skip.
# Rationale: BANK_OPERATIONAL has 3/5 tenant coverage (Fase C0); making
# it precondition-required would block create on 2 tenants for the cash
# path, which never needs it.
#
# VAT_OUTPUT is interim-mapped to 2-10300 (Hutang Pajak); see
# docs/MAPPING-ROLE-AKUN-LOCKED.md. Fase D will split.
SALES_RECEIPT_REQUIRED_ROLES = [
    AccountRole.CASH_GENERAL,
    AccountRole.REVENUE_SALES_GOODS,
    AccountRole.VAT_OUTPUT,
    AccountRole.COGS_SALES,
    AccountRole.INVENTORY_MERCHANDISE,
]

# Per-tenant precondition cache. Audit runs once per tenant per process at
# first posting-path call. Rationale: this is an architectural invariant (every
# tenant must be mapped) — repeating per request adds a tenant-table SELECT
# to a hot path. After first successful check the audit is skipped; a
# tenant added later without mapping would still fail loud at
# resolve_account_id_by_role(...) via AccountRoleUnmappedError.
# FIX_ROLE_PRECOND_PER_TENANT (2026-06-16): scope the audit to the ACTING
# tenant so one misconfigured tenant cannot fail-loud-block posting for all.
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition for the ACTING tenant on sales_receipts.

    Fails loud (PreconditionFailedError) if THIS tenant lacks any required
    role mapping. With tenant_id=None falls back to the legacy all-tenant
    audit. After first successful check the per-tenant audit is skipped.
    """
    if tenant_id is None:
        await assert_required_roles_for_path(
            pool, "sales_receipts", SALES_RECEIPT_REQUIRED_ROLES
        )
        return
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "sales_receipts", SALES_RECEIPT_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


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
    unit_price = Decimal(str(item["unit_price"]))
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

    subtotal = (quantity * unit_price).quantize(Decimal("0.01"))
    discount_amount = (subtotal * discount_percent / Decimal("100")).quantize(
        Decimal("0.01")
    )
    after_discount = subtotal - discount_amount
    tax_amount = (after_discount * tax_rate / Decimal("100")).quantize(Decimal("0.01"))
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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

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

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM sales_receipts sr WHERE {where_sql}", *params
        )

        rows = await conn.fetch(
            f"""
            SELECT sr.*, w.name as warehouse_name
            FROM sales_receipts sr
            LEFT JOIN warehouses w ON sr.warehouse_id = w.id
            WHERE {where_sql}
            ORDER BY sr.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params,
            limit,
            skip,
        )

        data = [SalesReceiptData(**dict(row)) for row in rows]
        return SalesReceiptListResponse(
            data=data, total=total, has_more=(skip + limit) < total
        )


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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            "SELECT * FROM get_daily_sales_summary($1, $2, $3)",
            ctx["tenant_id"],
            summary_date,
            warehouse_id,
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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            """
            SELECT sr.*, w.name as warehouse_name
            FROM sales_receipts sr
            LEFT JOIN warehouses w ON sr.warehouse_id = w.id
            WHERE sr.id = $1 AND sr.tenant_id = $2
            """,
            receipt_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Sales receipt not found")

        items = await conn.fetch(
            "SELECT * FROM sales_receipt_items WHERE sales_receipt_id = $1 ORDER BY line_number",
            receipt_id,
        )

        data = SalesReceiptDetailData(
            **dict(row), items=[SalesReceiptItemData(**dict(item)) for item in items]
        )

        return SalesReceiptDetailResponse(data=data)


@router.post("", response_model=CreateSalesReceiptResponse)
async def create_sales_receipt(request: Request, body: CreateSalesReceiptRequest):
    """Create sales receipt - atomic operation with journal entries"""
    ctx = get_user_context(request)
    pool = await get_pool()
    await _ensure_role_preconditions(pool, ctx["tenant_id"])

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
            )

            # Law 13: Advisory lock
            receipt_lock_key = f"SALES_RECEIPT:{uuid4()}"
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", receipt_lock_key
            )

            # Generate receipt number
            receipt_number = await conn.fetchval(
                "SELECT generate_sales_receipt_number($1)", ctx["tenant_id"]
            )

            # Get default warehouse if not provided
            warehouse_id = body.warehouse_id
            if not warehouse_id:
                warehouse_id = await conn.fetchval(
                    "SELECT get_default_warehouse($1)", ctx["tenant_id"]
                )

            # Calculate totals
            subtotal = 0
            tax_amount = 0
            total_cost = 0
            processed_items = []

            for idx, item in enumerate(body.items, 1):
                line_calc = calculate_line_totals(item.model_dump())

                # Get product cost from products table
                prod_row = await conn.fetchrow(
                    "SELECT purchase_price, item_code, nama_produk, item_type, track_inventory FROM products WHERE id = $1",
                    item.item_id,
                )
                unit_cost = (
                    Decimal(str(prod_row["purchase_price"]))
                    if prod_row and prod_row["purchase_price"]
                    else Decimal("0")
                )
                item_total_cost = (Decimal(str(item.quantity)) * unit_cost).quantize(
                    Decimal("0.01")
                )
                item_code_from_db = prod_row["item_code"] if prod_row else None
                item_name_from_db = prod_row["nama_produk"] if prod_row else None
                track_inventory = prod_row["track_inventory"] if prod_row else True

                processed_items.append(
                    {
                        **item.model_dump(),
                        **line_calc,
                        "unit_cost": unit_cost,
                        "total_cost": item_total_cost,
                        "line_number": idx,
                        "item_code_from_db": item_code_from_db,
                        "item_name_from_db": item_name_from_db,
                        "track_inventory": track_inventory,
                    }
                )

                subtotal += line_calc["subtotal"]
                tax_amount += line_calc["tax_amount"]
                total_cost += item_total_cost

            # Apply header discount (Law 25: Decimal precision)
            discount_amount = Decimal(str(body.discount_amount))
            if body.discount_percent > 0:
                discount_amount = (
                    Decimal(str(subtotal))
                    * Decimal(str(body.discount_percent))
                    / Decimal("100")
                ).quantize(Decimal("0.01"))

            total_amount = subtotal - discount_amount + tax_amount
            change_amount = body.amount_received - total_amount

            if change_amount < 0:
                raise HTTPException(
                    status_code=400, detail="Amount received is less than total"
                )

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
                ctx["tenant_id"],
                receipt_number,
                body.receipt_date,
                body.receipt_time,
                body.customer_id,
                body.customer_name,
                body.customer_phone,
                body.customer_email,
                warehouse_id,
                subtotal,
                body.discount_percent,
                discount_amount,
                tax_amount,
                total_amount,
                body.payment_method,
                body.payment_reference,
                body.amount_received,
                change_amount,
                body.bank_account_id,
                body.pos_terminal,
                body.shift_number,
                body.notes,
                body.internal_notes,
                ctx.get("user_id"),
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
                    receipt_id,
                    item["item_id"],
                    item.get("item_code"),
                    item["item_name"],
                    item.get("description"),
                    item["quantity"],
                    item.get("unit"),
                    item["unit_price"],
                    item.get("discount_percent", 0),
                    item["discount_amount"],
                    item.get("tax_id"),
                    item.get("tax_rate", 0),
                    item["tax_amount"],
                    item["subtotal"],
                    item["line_total"],
                    item["unit_cost"],
                    item["total_cost"],
                    item.get("batch_id"),
                    item.get("batch_number"),
                    item.get("serial_ids"),
                    item["line_number"],
                )
                items_data.append(SalesReceiptItemData(**dict(item_row)))

                # Update inventory (reduce stock)
                if warehouse_id and item.get("track_inventory", True):
                    # Get current balance
                    current_balance = await conn.fetchval(
                        "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
                        ctx["tenant_id"],
                        item["item_id"],
                    )
                    new_balance = current_balance - item["quantity"]
                    await conn.execute(
                        """
                        INSERT INTO inventory_ledger (
                            tenant_id, product_id, product_code, product_name,
                            movement_type, movement_date, source_type, source_id,
                            quantity_in, quantity_out, quantity_balance,
                            unit_cost, total_cost, average_cost,
                            warehouse_id, created_by, notes
                        ) VALUES (
                            $1, $2, $3, $4,
                            'SALE', $5, 'POS_SALE', $6,
                            0, $7, $8,
                            $9, $10, $9,
                            $11, $12, $13
                        )
                        """,
                        ctx["tenant_id"],
                        item["item_id"],
                        item.get("item_code_from_db") or item.get("item_code"),
                        item.get("item_name_from_db") or item.get("item_name"),
                        body.receipt_date,
                        receipt_id,
                        item["quantity"],
                        new_balance,
                        item["unit_cost"],
                        item["total_cost"],
                        warehouse_id,
                        ctx.get("user_id"),
                        f"Sales Receipt {receipt_number}",
                    )

            # Law 5: Period check before journal creation
            period = await conn.fetchrow(
                "SELECT id, period_name, status FROM fiscal_periods WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date ORDER BY start_date DESC LIMIT 1",
                ctx["tenant_id"],
                body.receipt_date,
            )
            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
                )

            # Create journal entries
            # 1. Sales Journal (Law 20: DRAFT→lines→POSTED)
            journal_id = uuid4()
            journal_number = f"SR-JE-{receipt_number}"

            # Fase C1.2: Determine cash/bank account via role resolver.
            # Primary source = user-picked bank_account_id (CoA via
            # bank_accounts.coa_id). Role is fallback ONLY:
            #   - payment_method == cash         -> CASH_GENERAL
            #   - other and no bank_account_id   -> BANK_OPERATIONAL (best-effort)
            # NULL guard: if everything fails to resolve, raise 422 with a
            # clear message. Previously this silently SKIPPED the Dr Kas/Bank
            # line, producing an unbalanced journal (Law 4 violation).
            cash_acct = None
            if body.bank_account_id:
                cash_acct = await conn.fetchval(
                    "SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                    body.bank_account_id,
                    ctx["tenant_id"],
                )
            if not cash_acct:
                if body.payment_method == "cash":
                    # Required role — gate guarantees coverage; raises
                    # AccountRoleUnmappedError if any future regression.
                    cash_acct = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.CASH_GENERAL
                    )
                else:
                    # Non-cash without picked bank account: try fallback,
                    # but raise 422 (NOT silent skip) if unmapped.
                    try:
                        cash_acct = await resolve_account_id_by_role(
                            conn, ctx["tenant_id"], AccountRole.BANK_OPERATIONAL
                        )
                    except Exception:
                        cash_acct = None
            if not cash_acct:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Akun kas/bank tidak tersedia. Pilih bank_account_id "
                        "atau konfigurasi role mapping (CASH_GENERAL / "
                        "BANK_OPERATIONAL) untuk tenant ini."
                    ),
                )

            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'SALES_RECEIPT', $6, 'DRAFT', $7, $7, $8)
                """,
                journal_id,
                ctx["tenant_id"],
                journal_number,
                body.receipt_date,
                f"Sales Receipt {receipt_number}",
                receipt_id,
                total_amount,
                ctx.get("user_id"),
            )

            # Journal lines
            line_num = 1
            # DR Cash/Bank — Law 4: cash_acct is guaranteed non-null by the
            # resolution block above; emission is unconditional. Previously
            # this was guarded by `if cash_acct:` which silently produced an
            # unbalanced journal when resolution failed.
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                VALUES ($1, $2, $3, $4, $5, 0, $6)
                """,
                uuid4(),
                journal_id,
                cash_acct,
                line_num,
                total_amount,
                f"Receipt {receipt_number}",
            )
            line_num += 1

            # CR Sales (Fase C1.2: role resolver)
            sales_acct = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.REVENUE_SALES_GOODS
            )
            if sales_acct:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                    uuid4(),
                    journal_id,
                    sales_acct,
                    line_num,
                    subtotal - discount_amount,
                    "Sales",
                )
                line_num += 1

            # CR Tax (if any) — Fase C1.2: role resolver (VAT_OUTPUT interim 2-10300)
            if tax_amount > 0:
                tax_acct = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.VAT_OUTPUT
                )
                if tax_acct:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        uuid4(),
                        journal_id,
                        tax_acct,
                        line_num,
                        tax_amount,
                        "PPN Keluaran",
                    )

            # Law 20: Promote to POSTED after all lines inserted
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )

            # 2. COGS Journal (if has cost) — Law 20: DRAFT→lines→POSTED
            cogs_journal_id = None
            if total_cost > 0:
                cogs_journal_id = uuid4()
                cogs_journal_number = f"SR-COGS-{receipt_number}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'SALES_RECEIPT_COGS', $6, 'DRAFT', $7, $7, $8)
                    """,
                    cogs_journal_id,
                    ctx["tenant_id"],
                    cogs_journal_number,
                    body.receipt_date,
                    f"COGS for {receipt_number}",
                    receipt_id,
                    total_cost,
                    ctx.get("user_id"),
                )

                # DR HPP (Fase C1.2: role resolver)
                hpp_acct = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.COGS_SALES
                )
                if hpp_acct:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                        VALUES ($1, $2, $3, 1, $4, 0, $5)
                        """,
                        uuid4(),
                        cogs_journal_id,
                        hpp_acct,
                        total_cost,
                        "Cost of Goods Sold",
                    )

                # CR Inventory (Fase C1.2: role resolver)
                inv_acct = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.INVENTORY_MERCHANDISE
                )
                if inv_acct:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                        VALUES ($1, $2, $3, 2, 0, $4, $5)
                        """,
                        uuid4(),
                        cogs_journal_id,
                        inv_acct,
                        total_cost,
                        "Inventory reduction",
                    )

                # Law 20: Promote COGS journal to POSTED
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    cogs_journal_id,
                )

            # Update receipt with journal IDs
            await conn.execute(
                "UPDATE sales_receipts SET journal_id = $1, cogs_journal_id = $2 WHERE id = $3",
                journal_id,
                cogs_journal_id,
                receipt_id,
            )

            # Link inventory_ledger entries to COGS journal
            if cogs_journal_id:
                await conn.execute(
                    "UPDATE inventory_ledger SET journal_id = $1 WHERE source_type = 'POS_SALE' AND source_id = $2 AND tenant_id = $3",
                    cogs_journal_id,
                    receipt_id,
                    ctx["tenant_id"],
                )

            # Fetch final receipt
            final_row = await conn.fetchrow(
                """
                SELECT sr.*, w.name as warehouse_name
                FROM sales_receipts sr
                LEFT JOIN warehouses w ON sr.warehouse_id = w.id
                WHERE sr.id = $1
                """,
                receipt_id,
            )

            data = SalesReceiptDetailData(**dict(final_row), items=items_data)

            return CreateSalesReceiptResponse(
                data=data, journal_id=journal_id, cogs_journal_id=cogs_journal_id
            )


@router.post("/{receipt_id}/void", response_model=VoidSalesReceiptResponse)
async def void_sales_receipt(
    request: Request, receipt_id: UUID, body: VoidSalesReceiptRequest
):
    """Void a sales receipt - creates reversing journal entries (SR8 compliant)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
            )

            # Law 13: Advisory lock for void
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"SALES_RECEIPT_VOID:{receipt_id}",
            )

            existing = await conn.fetchrow(
                "SELECT * FROM sales_receipts WHERE id = $1 AND tenant_id = $2",
                receipt_id,
                ctx["tenant_id"],
            )

            if not existing:
                raise HTTPException(status_code=404, detail="Sales receipt not found")

            if existing["status"] == "void":
                raise HTTPException(status_code=400, detail="Receipt already voided")

            # Law 5: Period check for void date
            void_date = date.today()
            period = await conn.fetchrow(
                "SELECT id, period_name, status FROM fiscal_periods WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date ORDER BY start_date DESC LIMIT 1",
                ctx["tenant_id"],
                void_date,
            )
            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
                )

            # SR8: Create proper reversal journals (not just mark as VOID)
            # 1. Reverse the sales journal
            if existing["journal_id"]:
                original_je = await conn.fetchrow(
                    "SELECT * FROM journal_entries WHERE id = $1 AND status = 'POSTED' AND tenant_id = $2",
                    existing["journal_id"],
                    ctx["tenant_id"],
                )
                if original_je:
                    original_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                        existing["journal_id"],
                    )
                    # Create reversal journal (Law 20: DRAFT→lines→POSTED)
                    rev_sales_id = uuid4()
                    rev_sales_number = f"SR-REV-{existing['receipt_number']}"
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date, description,
                            source_type, source_id, status, total_debit, total_credit,
                            reversal_of_id, reversal_reason, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'SALES_RECEIPT', $6, 'DRAFT', $7, $7, $8, $9, $10)
                        """,
                        rev_sales_id,
                        ctx["tenant_id"],
                        rev_sales_number,
                        void_date,
                        f"Reversal: Sales Receipt {existing['receipt_number']}",
                        receipt_id,
                        original_je["total_debit"],
                        existing["journal_id"],
                        body.reason,
                        ctx.get("user_id"),
                    )
                    # Swap debits and credits
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            """,
                            uuid4(),
                            rev_sales_id,
                            line["account_id"],
                            idx,
                            line["credit"],
                            line["debit"],  # Swapped
                            f"Reversal: {line['memo'] or ''}",
                        )
                    # Law 20: Promote to POSTED
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        rev_sales_id,
                    )
                    # Link reversal
                    await conn.execute(
                        "UPDATE journal_entries SET reversed_by_id = $1, reversed_at = NOW() WHERE id = $2",
                        rev_sales_id,
                        existing["journal_id"],
                    )

            # 2. Reverse the COGS journal
            rev_cogs_id = None
            if existing["cogs_journal_id"]:
                cogs_je = await conn.fetchrow(
                    "SELECT * FROM journal_entries WHERE id = $1 AND status = 'POSTED' AND tenant_id = $2",
                    existing["cogs_journal_id"],
                    ctx["tenant_id"],
                )
                if cogs_je:
                    cogs_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                        existing["cogs_journal_id"],
                    )
                    rev_cogs_id = uuid4()
                    rev_cogs_number = f"SR-COGS-REV-{existing['receipt_number']}"
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date, description,
                            source_type, source_id, status, total_debit, total_credit,
                            reversal_of_id, reversal_reason, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'SALES_RECEIPT_COGS', $6, 'DRAFT', $7, $7, $8, $9, $10)
                        """,
                        rev_cogs_id,
                        ctx["tenant_id"],
                        rev_cogs_number,
                        void_date,
                        f"Reversal: COGS {existing['receipt_number']}",
                        receipt_id,
                        cogs_je["total_debit"],
                        existing["cogs_journal_id"],
                        body.reason,
                        ctx.get("user_id"),
                    )
                    for idx, line in enumerate(cogs_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            """,
                            uuid4(),
                            rev_cogs_id,
                            line["account_id"],
                            idx,
                            line["credit"],
                            line["debit"],
                            f"Reversal: {line['memo'] or ''}",
                        )
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        rev_cogs_id,
                    )
                    await conn.execute(
                        "UPDATE journal_entries SET reversed_by_id = $1, reversed_at = NOW() WHERE id = $2",
                        rev_cogs_id,
                        existing["cogs_journal_id"],
                    )

            # 3. Reverse inventory with journal linking
            items = await conn.fetch(
                "SELECT * FROM sales_receipt_items WHERE sales_receipt_id = $1",
                receipt_id,
            )

            for item in items:
                if existing["warehouse_id"]:
                    prod_info = await conn.fetchrow(
                        "SELECT item_code, nama_produk, track_inventory FROM products WHERE id = $1",
                        item["item_id"],
                    )
                    if not prod_info or not prod_info.get("track_inventory", True):
                        continue
                    current_balance = await conn.fetchval(
                        "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
                        ctx["tenant_id"],
                        item["item_id"],
                    )
                    new_balance = current_balance + item["quantity"]
                    await conn.execute(
                        """
                        INSERT INTO inventory_ledger (
                            tenant_id, product_id, product_code, product_name,
                            movement_type, movement_date, source_type, source_id,
                            quantity_in, quantity_out, quantity_balance,
                            unit_cost, total_cost, average_cost,
                            warehouse_id, journal_id, created_by, notes
                        ) VALUES (
                            $1, $2, $3, $4,
                            'SALE_VOID', $5, 'POS_SALE_VOID', $6,
                            $7, 0, $8,
                            $9, $10, $9,
                            $11, $12, $13, $14
                        )
                        """,
                        ctx["tenant_id"],
                        item["item_id"],
                        prod_info["item_code"] if prod_info else None,
                        prod_info["nama_produk"] if prod_info else item["item_name"],
                        void_date,
                        receipt_id,
                        item["quantity"],
                        new_balance,
                        item["unit_cost"],
                        item["total_cost"],
                        existing["warehouse_id"],
                        rev_cogs_id,
                        ctx.get("user_id"),
                        f"Void Sales Receipt {existing['receipt_number']}",
                    )

            # Update receipt status
            row = await conn.fetchrow(
                """
                UPDATE sales_receipts
                SET status = 'void', voided_at = NOW(), voided_by = $2, void_reason = $3, updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                receipt_id,
                ctx.get("user_id"),
                body.reason,
            )

            return VoidSalesReceiptResponse(data=SalesReceiptData(**dict(row)))
