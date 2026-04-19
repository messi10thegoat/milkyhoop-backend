"""
Expenses Router - Biaya & Pengeluaran Management

Endpoints for managing expenses with auto journal posting.
Supports single and itemized expenses with PPh withholding.
"""

from fastapi import APIRouter, Body, HTTPException, Request, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any
from uuid import UUID
from datetime import date
from decimal import Decimal
import logging
import asyncpg

from ..schemas.expenses import (
    CreateExpenseRequest,
    UpdateExpenseRequest,
    VoidExpenseRequest,
    ExpenseListResponse,
    ExpenseDetailResponse,
    CreateExpenseResponse,
    UpdateExpenseResponse,
    DeleteExpenseResponse,
    VoidExpenseResponse,
    ExpenseSummaryResponse,
    CalculateExpenseResponse,
    ExpenseAutocompleteResponse,
)
from ..services.resolve_account import resolve_account_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (initialized on first request)


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


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


# =============================================================================
# HEALTH CHECK
# =============================================================================


# === Journal Preview (read-only, for DirectAction confirmation card) ===


@router.post("/preview-journal")
async def preview_journal(request: Request, body: dict = Body(...)):
    """
    Dry-run journal preview for expense. Returns debit/credit lines WITHOUT posting.
    Used by DirectAction confirmation card to show accounting impact.
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        amount = float(body.get("amount", 0) or 0)
        tax_rate = float(body.get("tax_rate", 0) or 0)
        paid_through_id = body.get("paid_through_id", "")
        account_id = body.get("account_id", "")

        subtotal = amount
        tax_amount = subtotal * tax_rate / 100.0 if tax_rate else 0

        lines = []

        # Dr. Expense account
        expense_name = "Beban"
        expense_code = ""
        if account_id:
            try:
                from uuid import UUID

                row = await conn.fetchrow(
                    "SELECT name, account_code FROM chart_of_accounts WHERE id = $1::uuid AND tenant_id = $2",
                    UUID(account_id),
                    ctx["tenant_id"],
                )
                if row:
                    expense_name = row["name"]
                    expense_code = row["account_code"]
            except Exception:
                pass
        lines.append(
            {
                "account_name": expense_name,
                "account_code": expense_code,
                "debit": subtotal,
                "credit": 0,
            }
        )

        # Dr. PPN Masukan (if tax)
        if tax_amount > 0:
            ppn_name = "PPN Masukan"
            ppn_code = "1-10800"
            try:
                row = await conn.fetchrow(
                    "SELECT name FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10800' AND is_active = true",
                    ctx["tenant_id"],
                )
                if row:
                    ppn_name = row["name"]
            except Exception:
                pass
            lines.append(
                {
                    "account_name": ppn_name,
                    "account_code": ppn_code,
                    "debit": tax_amount,
                    "credit": 0,
                }
            )

        # Cr. Bank/Kas
        bank_name = "Bank/Kas"
        bank_code = ""
        if paid_through_id:
            try:
                from uuid import UUID

                ba_row = await conn.fetchrow(
                    """SELECT ba.coa_id, ca.name, ca.account_code
                       FROM bank_accounts ba
                       JOIN chart_of_accounts ca ON ca.id = ba.coa_id AND ca.tenant_id = ba.tenant_id
                       WHERE ba.id = $1::uuid AND ba.tenant_id = $2""",
                    UUID(paid_through_id),
                    ctx["tenant_id"],
                )
                if ba_row:
                    bank_name = ba_row["name"]
                    bank_code = ba_row["account_code"]
            except Exception:
                pass
        total_credit = subtotal + tax_amount
        lines.append(
            {
                "account_name": bank_name,
                "account_code": bank_code,
                "debit": 0,
                "credit": total_credit,
            }
        )

        return {"journal_lines": lines}


@router.get("/health")
async def health_check():
    """Health check endpoint for the expenses service."""
    return {"status": "ok", "service": "expenses"}


# =============================================================================
# GET SUMMARY
# =============================================================================
@router.get("/summary", response_model=ExpenseSummaryResponse)
async def get_expenses_summary(
    request: Request,
    period: Literal["week", "month", "quarter", "year"] = Query(
        "month", description="Period for summary"
    ),
):
    """
    Get expense summary statistics for dashboard (Iron Law 1 compliant).

    All financial amounts derived from journal_lines.
    Metadata (vendor_count, billable) from expenses table.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Date filter based on period (applied to journal_date)
            date_filter_journal = {
                "week": "je.journal_date >= CURRENT_DATE - INTERVAL '7 days'",
                "month": "je.journal_date >= DATE_TRUNC('month', CURRENT_DATE)",
                "quarter": "je.journal_date >= DATE_TRUNC('quarter', CURRENT_DATE)",
                "year": "je.journal_date >= DATE_TRUNC('year', CURRENT_DATE)",
            }[period]

            # Same date filter for expenses table (metadata queries)
            date_filter_expense = {
                "week": "e.expense_date >= CURRENT_DATE - INTERVAL '7 days'",
                "month": "e.expense_date >= DATE_TRUNC('month', CURRENT_DATE)",
                "quarter": "e.expense_date >= DATE_TRUNC('quarter', CURRENT_DATE)",
                "year": "e.expense_date >= DATE_TRUNC('year', CURRENT_DATE)",
            }[period]

            # Iron Law 1: Financial amounts from journal_lines
            journal_summary = await conn.fetchrow(
                f"""
                WITH expense_journals AS (
                    SELECT je.id, je.source_id
                    FROM journal_entries je
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND je.source_type = 'expense'
                      AND je.reversal_of_id IS NULL
                      AND je.reversed_by_id IS NULL
                      AND {date_filter_journal}
                )
                SELECT
                    COUNT(DISTINCT ej.source_id) as total_count,
                    COALESCE(SUM(jl.debit), 0) as total_amount
                FROM expense_journals ej
                JOIN journal_lines jl ON jl.journal_id = ej.id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE', 'COGS')
                  AND jl.debit > 0
            """,
                ctx["tenant_id"],
            )

            # Tax total from journal_lines (PPN Masukan = account 1-10800, ASSET type)
            total_tax = (
                await conn.fetchval(
                    f"""
                WITH expense_journals AS (
                    SELECT je.id
                    FROM journal_entries je
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND je.source_type = 'expense'
                      AND je.reversal_of_id IS NULL
                      AND je.reversed_by_id IS NULL
                      AND {date_filter_journal}
                )
                SELECT COALESCE(SUM(jl.debit), 0)
                FROM expense_journals ej
                JOIN journal_lines jl ON jl.journal_id = ej.id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE coa.account_code = '1-10800'
                  AND jl.debit > 0
            """,
                    ctx["tenant_id"],
                )
                or 0
            )

            # Metadata from expenses table (non-financial: counts only)
            metadata = await conn.fetchrow(
                f"""
                SELECT
                    COUNT(DISTINCT vendor_id) as vendor_count,
                    COUNT(CASE WHEN is_billable THEN 1 END) as billable_count
                FROM expenses e
                WHERE e.tenant_id = $1 AND e.status = 'posted' AND {date_filter_expense}
            """,
                ctx["tenant_id"],
            )

            # Billable amount from journal_lines (only EXPENSE source_type, billable)
            billable_amount = (
                await conn.fetchval(
                    f"""
                WITH billable_journals AS (
                    SELECT je.id
                    FROM journal_entries je
                    JOIN expenses e ON e.journal_id = je.id AND e.tenant_id = $1
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND je.source_type = 'expense'
                      AND je.reversal_of_id IS NULL
                      AND je.reversed_by_id IS NULL
                      AND e.is_billable = true
                      AND {date_filter_journal}
                )
                SELECT COALESCE(SUM(jl.debit), 0)
                FROM billable_journals bj
                JOIN journal_lines jl ON jl.journal_id = bj.id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE', 'COGS')
                  AND jl.debit > 0
            """,
                    ctx["tenant_id"],
                )
                or 0
            )

            # Top expense accounts from journal_lines
            top_accounts = await conn.fetch(
                f"""
                WITH expense_journals AS (
                    SELECT je.id
                    FROM journal_entries je
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND je.source_type = 'expense'
                      AND je.reversal_of_id IS NULL
                      AND je.reversed_by_id IS NULL
                      AND {date_filter_journal}
                )
                SELECT
                    coa.id as account_id,
                    coa.name as account_name,
                    COALESCE(SUM(jl.debit), 0) as total_amount,
                    COUNT(DISTINCT ej.id) as count
                FROM expense_journals ej
                JOIN journal_lines jl ON jl.journal_id = ej.id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE', 'COGS')
                  AND jl.debit > 0
                GROUP BY coa.id, coa.name
                ORDER BY total_amount DESC
                LIMIT 5
            """,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "data": {
                    "period": period,
                    "total_count": journal_summary["total_count"],
                    "total_amount": journal_summary["total_amount"],
                    "total_tax": total_tax,
                    "vendor_count": metadata["vendor_count"],
                    "billable_count": metadata["billable_count"],
                    "billable_amount": billable_amount,
                    "top_accounts": [
                        {
                            "account_id": r["account_id"],
                            "account_name": r["account_name"],
                            "total_amount": r["total_amount"],
                            "count": r["count"],
                        }
                        for r in top_accounts
                    ],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting expense summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# CALCULATE (Preview)
# =============================================================================
@router.post("/calculate", response_model=CalculateExpenseResponse)
async def calculate_expense_totals(request: Request, body: CreateExpenseRequest):
    """
    Preview expense calculation without saving.

    Use this endpoint to show calculated totals in the UI before submitting.
    """
    try:
        get_user_context(request)  # Validate auth

        # Calculate subtotal
        if body.is_itemized and body.line_items:
            subtotal = sum(item.amount for item in body.line_items)
        else:
            subtotal = body.amount or 0

        # Calculate tax and PPh
        tax_amount = (
            Decimal(str(subtotal)) * Decimal(str(body.tax_rate or 0)) / Decimal("100")
        ).quantize(Decimal("1"))
        pph_amount = (
            Decimal(str(subtotal)) * Decimal(str(body.pph_rate or 0)) / Decimal("100")
        ).quantize(Decimal("1"))
        total_amount = subtotal + tax_amount - pph_amount

        return {
            "success": True,
            "calculation": {
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "pph_amount": pph_amount,
                "total_amount": total_amount,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating expense: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to calculate")


# =============================================================================
# LIST EXPENSES
# =============================================================================
@router.get("", response_model=ExpenseListResponse)
async def list_expenses(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[Literal["all", "draft", "posted", "void"]] = Query(
        "all", description="Filter by status"
    ),
    vendor_id: Optional[UUID] = Query(None, description="Filter by vendor"),
    account_id: Optional[UUID] = Query(None, description="Filter by expense account"),
    date_from: Optional[date] = Query(None, description="Filter date from"),
    date_to: Optional[date] = Query(None, description="Filter date to"),
    search: Optional[str] = Query(
        None, description="Search expense number, vendor, or notes"
    ),
    sort_by: Literal[
        "expense_date", "created_at", "total_amount", "vendor_name"
    ] = Query("expense_date", description="Sort field"),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
    is_billable: Optional[bool] = Query(None, description="Filter by billable status"),
    billing_status: Optional[Literal["billable", "non_billable", "invoiced"]] = Query(
        None, description="Filter by billing status: billable, non_billable, invoiced"
    ),
):
    """
    List expenses with filtering, sorting, and pagination.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Build WHERE clause
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(str(vendor_id))
                param_idx += 1

            if account_id:
                conditions.append(f"account_id = ${param_idx}")
                params.append(str(account_id))
                param_idx += 1

            if date_from:
                conditions.append(f"expense_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"expense_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            if is_billable is not None:
                conditions.append(f"is_billable = ${param_idx}")
                params.append(is_billable)
                param_idx += 1

            if billing_status:
                if billing_status == "billable":
                    conditions.append(
                        "is_billable = true AND billed_invoice_id IS NULL"
                    )
                elif billing_status == "non_billable":
                    conditions.append("is_billable = false")
                elif billing_status == "invoiced":
                    conditions.append("billed_invoice_id IS NOT NULL")

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(expense_number ILIKE ${param_idx} OR vendor_name ILIKE ${param_idx} OR notes ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(expense_number ILIKE ${param_idx} OR vendor_name ILIKE ${param_idx} OR notes ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            where_clause = " AND ".join(conditions)

            # Validate sort_by to prevent SQL injection
            valid_sort = {
                "expense_date": "expense_date",
                "created_at": "created_at",
                "total_amount": "total_amount",
                "vendor_name": "vendor_name",
            }
            sort_column = valid_sort.get(sort_by, "expense_date")

            # Count total
            count_query = f"SELECT COUNT(*) FROM expenses WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Fetch expenses
            query = f"""
                SELECT
                    id, expense_number, expense_date,
                    paid_through_id, paid_through_name,
                    vendor_id, vendor_name,
                    account_id, account_name,
                    subtotal, tax_amount, total_amount,
                    is_itemized, status, is_billable,
                    billed_invoice_id, billed_to_customer_id,
                    accounting_status,
                    reference, notes, has_receipt,
                    created_at
                FROM expenses
                WHERE {where_clause}
                ORDER BY {sort_column} {sort_order.upper()}, created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = []
            for row in rows:
                items.append(
                    {
                        "id": row["id"],
                        "expense_number": row["expense_number"],
                        "expense_date": row["expense_date"],
                        "paid_through_name": row["paid_through_name"],
                        "vendor": {"id": row["vendor_id"], "name": row["vendor_name"]}
                        if row["vendor_id"]
                        else None,
                        "account_name": row["account_name"],
                        "subtotal": row["subtotal"],
                        "tax_amount": row["tax_amount"],
                        "total_amount": row["total_amount"],
                        "is_itemized": row["is_itemized"] or False,
                        "status": row["status"],
                        "is_billable": row["is_billable"] or False,
                        "has_receipt": row["has_receipt"] or False,
                        "billed_invoice_id": str(row["billed_invoice_id"])
                        if row["billed_invoice_id"]
                        else None,
                        "billed_to_customer_id": str(row["billed_to_customer_id"])
                        if row["billed_to_customer_id"]
                        else None,
                        "accounting_status": row["accounting_status"],
                        "reference": row["reference"],
                        "notes": row["notes"],
                        "created_at": row["created_at"],
                    }
                )

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing expenses: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list expenses")


# =============================================================================
# AUTOCOMPLETE
# =============================================================================
@router.get("/autocomplete", response_model=ExpenseAutocompleteResponse)
async def autocomplete_expenses(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
):
    """Fast search for expense autocomplete in forms."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            rows = await conn.fetch(
                """
                SELECT id, expense_number, expense_date, total_amount, vendor_name
                FROM expenses
                WHERE tenant_id = $1
                  AND status != 'void'
                  AND (expense_number ILIKE $2 OR vendor_name ILIKE $2)
                ORDER BY expense_date DESC
                LIMIT $3
            """,
                ctx["tenant_id"],
                f"%{q}%",
                limit,
            )

            return {
                "items": [
                    {
                        "id": row["id"],
                        "expense_number": row["expense_number"],
                        "expense_date": row["expense_date"],
                        "total_amount": row["total_amount"],
                        "vendor_name": row["vendor_name"],
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in expense autocomplete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


# =============================================================================
# LEDGER VIEW — All Expense-Account Debits (Law 16 Compliant)
# =============================================================================
@router.get("/ledger")
async def list_expense_ledger(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[Literal["all", "posted", "void"]] = Query(
        "all", description="Filter by effective status"
    ),
    date_from: Optional[date] = Query(None, description="Filter date from"),
    date_to: Optional[date] = Query(None, description="Filter date to"),
    search: Optional[str] = Query(
        None, description="Search description or expense number"
    ),
    sort_by: Literal["journal_date", "created_at", "amount"] = Query(
        "journal_date", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
    billing_status: Optional[Literal["billable", "non_billable", "invoiced"]] = Query(
        None, description="Filter by billing status (expenses-table entries only)"
    ),
    has_receipt: Optional[bool] = Query(None, description="Filter by receipt status"),
):
    """
    List ALL expense transactions from the journal ledger (Law 16 compliant).

    Returns journal entries that debit expense accounts (EXPENSE, OTHER_EXPENSE),
    enriched with metadata from the expenses table where available.

    Captures ALL expense-type transactions regardless of source module:
    - Expenses module (source_type = 'expense')
    - Kas & Bank module (source_type = 'BANK_TRANSACTION')
    - Reconciliation adjustments (source_type = 'RECONCILIATION_ADJUSTMENT')
    - Manual journals (source_type = 'MANUAL')
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Build dynamic WHERE conditions
            conditions = []
            params: list = [ctx["tenant_id"]]
            param_idx = 2

            # Date filters on journal_date
            if date_from:
                conditions.append(f"el.journal_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"el.journal_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            # Status filter (posted vs void/reversed)
            if status == "posted":
                conditions.append("el.is_reversed = false")
            elif status == "void":
                conditions.append("(el.is_reversed = true OR e.status = 'void')")

            # Billing status (only applies to expenses-table entries)
            if billing_status:
                if billing_status == "billable":
                    conditions.append(
                        "e.is_billable = true AND e.billed_invoice_id IS NULL"
                    )
                elif billing_status == "non_billable":
                    conditions.append("e.is_billable = false")
                elif billing_status == "invoiced":
                    conditions.append("e.billed_invoice_id IS NOT NULL")

            # Receipt filter (only applies to expenses-table entries)
            if has_receipt is not None:
                if has_receipt:
                    conditions.append("e.has_receipt = true")
                else:
                    conditions.append(
                        "(e.has_receipt = false OR e.has_receipt IS NULL)"
                    )

            # Search across expense number, vendor name, and journal description
            if search:
                conditions.append(
                    f"""(
                        e.expense_number ILIKE ${param_idx}
                        OR e.vendor_name ILIKE ${param_idx}
                        OR el.journal_description ILIKE ${param_idx}
                    )"""
                )
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = (" AND " + " AND ".join(conditions)) if conditions else ""

            # Sort mapping
            sort_map = {
                "journal_date": "el.journal_date",
                "created_at": "el.created_at",
                "amount": "el.total_debit_amount",
            }
            sort_col = sort_map.get(sort_by, "el.journal_date")
            sort_dir = sort_order.upper()

            # Main query: journal-based expense ledger
            query = f"""
                WITH expense_ledger AS (
                    SELECT
                        je.id as journal_id,
                        je.journal_number,
                        je.journal_date,
                        je.source_type,
                        je.source_id,
                        je.description as journal_description,
                        je.created_at,
                        -- Sum all expense-account debit lines
                        SUM(jl.debit) as total_debit_amount,
                        -- First account info (for display)
                        MIN(coa.name) as first_account_name,
                        MIN(coa.account_code) as first_account_code,
                        COUNT(DISTINCT jl.account_id) as account_count,
                        -- Check if reversed (voided)
                        EXISTS (
                            SELECT 1 FROM journal_entries rev
                            WHERE rev.reversal_of_id = je.id
                              AND rev.status = 'POSTED'
                        ) as is_reversed
                    FROM journal_entries je
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE')
                      AND jl.debit > 0
                      AND je.reversal_of_id IS NULL
                    GROUP BY je.id, je.journal_number, je.journal_date,
                             je.source_type, je.source_id, je.description,
                             je.created_at
                )
                SELECT
                    el.*,
                    -- Expenses table metadata (if exists)
                    e.id as expense_id,
                    e.expense_number,
                    e.vendor_id,
                    e.vendor_name,
                    e.paid_through_id,
                    e.paid_through_name,
                    e.account_name as expense_account_name,
                    e.is_billable,
                    e.has_receipt,
                    e.notes,
                    e.billed_invoice_id,
                    e.billed_to_customer_id,
                    e.status as expense_status,
                    e.accounting_status as expense_accounting_status,
                    e.tax_amount,
                    e.total_amount as expense_total_amount,
                    e.subtotal,
                    -- Credit-side account (paid through) for journal-only entries
                    credit_acct.paid_through_name as journal_paid_through
                FROM expense_ledger el
                LEFT JOIN expenses e
                    ON e.journal_id = el.journal_id AND e.tenant_id = $1
                LEFT JOIN LATERAL (
                    SELECT coa2.name as paid_through_name
                    FROM journal_lines jl2
                    JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                    WHERE jl2.journal_id = el.journal_id
                      AND jl2.credit > 0
                      AND coa2.account_type = 'ASSET'
                    ORDER BY jl2.credit DESC
                    LIMIT 1
                ) credit_acct ON true
                WHERE 1=1 {where_clause}
                ORDER BY {sort_col} {sort_dir}, el.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            # Count query (same CTE, just count)
            count_query = f"""
                WITH expense_ledger AS (
                    SELECT
                        je.id as journal_id,
                        je.journal_date,
                        je.source_type,
                        je.description as journal_description,
                        je.created_at,
                        EXISTS (
                            SELECT 1 FROM journal_entries rev
                            WHERE rev.reversal_of_id = je.id
                              AND rev.status = 'POSTED'
                        ) as is_reversed
                    FROM journal_entries je
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND coa.account_type IN ('EXPENSE', 'OTHER_EXPENSE')
                      AND jl.debit > 0
                      AND je.reversal_of_id IS NULL
                    GROUP BY je.id, je.journal_date, je.source_type,
                             je.description, je.created_at
                )
                SELECT COUNT(*) FROM expense_ledger el
                LEFT JOIN expenses e
                    ON e.journal_id = el.journal_id AND e.tenant_id = $1
                WHERE 1=1 {where_clause}
            """
            # Count uses same params minus limit/offset
            count_params = params[:-2]
            total = await conn.fetchval(count_query, *count_params)

            # Transform rows to response
            items = []
            for row in rows:
                has_expense_record = row["expense_id"] is not None

                # Determine effective status
                if row["is_reversed"] or (
                    has_expense_record and row["expense_status"] == "void"
                ):
                    effective_status = "void"
                else:
                    effective_status = "posted"

                # Build display title
                if has_expense_record and row["expense_number"]:
                    title = row["expense_number"]
                else:
                    title = row["journal_description"] or row["journal_number"]

                # Build account name
                account_name = (
                    row["expense_account_name"]
                    if has_expense_record and row["expense_account_name"]
                    else row["first_account_name"]
                )

                # Paid through info
                paid_through = (
                    row["paid_through_name"]
                    if has_expense_record and row["paid_through_name"]
                    else row["journal_paid_through"]
                )

                # Amount: from journal (Law 16)
                amount = float(row["total_debit_amount"])

                items.append(
                    {
                        "id": str(row["expense_id"])
                        if has_expense_record
                        else str(row["journal_id"]),
                        "expense_number": row["expense_number"]
                        if has_expense_record
                        else None,
                        "journal_number": row["journal_number"],
                        "expense_date": row["journal_date"],
                        "source_type": row["source_type"],
                        "paid_through_name": paid_through,
                        "vendor": {
                            "id": str(row["vendor_id"]),
                            "name": row["vendor_name"],
                        }
                        if has_expense_record and row["vendor_id"]
                        else None,
                        "vendor_name": row["vendor_name"]
                        if has_expense_record
                        else None,
                        "account_name": account_name,
                        "account_code": row["first_account_code"],
                        "account_count": row["account_count"],
                        "subtotal": float(row["subtotal"])
                        if has_expense_record and row["subtotal"]
                        else amount,
                        "tax_amount": float(row["tax_amount"])
                        if has_expense_record and row["tax_amount"]
                        else 0,
                        "total_amount": amount,
                        "is_itemized": False,
                        "status": effective_status,
                        "is_billable": row["is_billable"]
                        if has_expense_record
                        else False,
                        "has_receipt": row["has_receipt"]
                        if has_expense_record
                        else False,
                        "billed_invoice_id": str(row["billed_invoice_id"])
                        if has_expense_record and row["billed_invoice_id"]
                        else None,
                        "billed_to_customer_id": str(row["billed_to_customer_id"])
                        if has_expense_record and row["billed_to_customer_id"]
                        else None,
                        "accounting_status": row["expense_accounting_status"]
                        if has_expense_record
                        else effective_status,
                        "reference": None,
                        "notes": row["notes"]
                        if has_expense_record
                        else row["journal_description"],
                        "description": row["journal_description"],
                        "has_expense_record": has_expense_record,
                        "created_at": row["created_at"],
                    }
                )

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing expense ledger: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list expense ledger")


# =============================================================================
# GET EXPENSE DETAIL
# =============================================================================
@router.get("/{expense_id}", response_model=ExpenseDetailResponse)
async def get_expense_detail(request: Request, expense_id: UUID):
    """
    Get detailed information for a single expense.

    Includes line items (if itemized) and attachments.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Fetch expense
            expense = await conn.fetchrow(
                """
                SELECT * FROM expenses
                WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            result = dict(expense)

            # Fetch items if itemized
            if expense["is_itemized"]:
                items = await conn.fetch(
                    """
                    SELECT id, account_id, account_name, amount, notes, line_number
                    FROM expense_items
                    WHERE expense_id = $1
                    ORDER BY line_number
                """,
                    str(expense_id),
                )
                result["items"] = [dict(i) for i in items]
            else:
                result["items"] = []

            # Fetch attachments
            attachments = await conn.fetch(
                """
                SELECT id, file_name, file_path, file_size, mime_type, uploaded_at
                FROM expense_attachments
                WHERE expense_id = $1
            """,
                str(expense_id),
            )
            result["attachments"] = [dict(a) for a in attachments]

            return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting expense {expense_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get expense")


# =============================================================================
# CREATE EXPENSE
# =============================================================================
@router.post("", response_model=CreateExpenseResponse, status_code=201)
async def create_expense(request: Request, body: CreateExpenseRequest):
    """
    Create a new expense with auto journal posting.

    Creates:
    - Expense record
    - Line items (if itemized)
    - Journal entry (DR Expense, CR Cash/Bank)
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            async with conn.transaction():
                # Law 13: Advisory lock to prevent concurrent expense creation
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"EXPENSE_CREATE:{ctx['tenant_id']}:{body.expense_date}",
                )

                # Law 5: Check accounting period is open
                await check_period_is_open(conn, ctx["tenant_id"], body.expense_date)

                # Generate expense number
                expense_number = await conn.fetchval(
                    "SELECT generate_expense_number($1)", ctx["tenant_id"]
                )

                # Calculate totals
                if body.is_itemized and body.line_items:
                    subtotal = sum(item.amount for item in body.line_items)
                else:
                    subtotal = body.amount or 0

                # Validate subtotal is positive
                if subtotal <= 0:
                    raise HTTPException(
                        status_code=400, detail="Expense amount must be greater than 0"
                    )

                tax_amount = (
                    Decimal(str(subtotal))
                    * Decimal(str(body.tax_rate or 0))
                    / Decimal("100")
                ).quantize(Decimal("1"))
                pph_amount = (
                    Decimal(str(subtotal))
                    * Decimal(str(body.pph_rate or 0))
                    / Decimal("100")
                ).quantize(Decimal("1"))
                total_amount = subtotal + tax_amount - pph_amount

                # Get paid_through account info
                paid_through = await conn.fetchrow(
                    """
                    SELECT ba.id, ba.account_name, ba.coa_id, coa.name as coa_name
                    FROM bank_accounts ba
                    LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
                    WHERE ba.id = $1 AND ba.tenant_id = $2
                """,
                    str(body.paid_through_id),
                    ctx["tenant_id"],
                )

                if not paid_through:
                    raise HTTPException(
                        status_code=400, detail="Invalid paid_through account"
                    )

                # Auto-set has_receipt if attachments provided
                has_receipt = body.has_receipt or (
                    body.attachment_ids and len(body.attachment_ids) > 0
                )

                # Insert expense
                expense_id = await conn.fetchval(
                    """
                    INSERT INTO expenses (
                        tenant_id, expense_number, expense_date,
                        paid_through_id, paid_through_name, paid_through_coa_id,
                        vendor_id, vendor_name,
                        account_id, account_name,
                        currency, subtotal,
                        tax_id, tax_name, tax_rate, tax_amount,
                        pph_type, pph_rate, pph_amount,
                        total_amount, is_itemized, status,
                        is_billable, billed_to_customer_id, reference, notes, has_receipt,
                        created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19,
                        $20, $21, $22, $23, $24, $25, $26, $27, $28
                    ) RETURNING id
                """,
                    ctx["tenant_id"],
                    expense_number,
                    body.expense_date,
                    str(body.paid_through_id),
                    paid_through["account_name"],
                    paid_through["coa_id"],
                    str(body.vendor_id) if body.vendor_id else None,
                    body.vendor_name,
                    str(body.account_id) if body.account_id else None,
                    body.account_name,
                    body.currency or "IDR",
                    subtotal,
                    str(body.tax_id) if body.tax_id else None,
                    body.tax_name,
                    Decimal(str(body.tax_rate or 0)),
                    tax_amount,
                    body.pph_type,
                    Decimal(str(body.pph_rate or 0)),
                    pph_amount,
                    total_amount,
                    body.is_itemized,
                    "posted",
                    body.is_billable,
                    str(body.billed_to_customer_id)
                    if body.billed_to_customer_id
                    else None,
                    body.reference,
                    body.notes,
                    has_receipt,
                    str(ctx["user_id"]),
                )

                # Insert line items if itemized
                if body.is_itemized and body.line_items:
                    for idx, item in enumerate(body.line_items, 1):
                        await conn.execute(
                            """
                            INSERT INTO expense_items (
                                tenant_id, expense_id, account_id, account_name,
                                amount, notes, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            ctx["tenant_id"],
                            str(expense_id),
                            str(item.account_id),
                            item.account_name,
                            item.amount,
                            item.notes,
                            idx,
                        )

                # Create journal entry
                journal_id = await create_expense_journal(
                    conn,
                    ctx["tenant_id"],
                    expense_id,
                    expense_number,
                    body.expense_date,
                    body.is_itemized,
                    body.account_id,
                    body.line_items,
                    subtotal,
                    tax_amount,
                    pph_amount,
                    paid_through["coa_id"],
                    body.tax_id,
                    pph_type=body.pph_type,
                    pph_rate=body.pph_rate,
                    vendor_id=body.vendor_id,
                )

                # Update expense with journal_id + mark accounting_status POSTED
                await conn.execute(
                    """
                    UPDATE expenses
                    SET journal_id = $1, accounting_status = 'POSTED'
                    WHERE id = $2
                    """,
                    str(journal_id),
                    str(expense_id),
                )

                # Create bank transaction to update bank balance
                import uuid as uuid_module

                bank_tx_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date,
                        transaction_type, amount, running_balance,
                        reference_type, reference_id, description,
                        payee_payer, created_by, journal_id
                    ) VALUES ($1, $2, $3, $4, 'withdrawal', $5, 0, 'expense', $6, $7, $8, $9, $10)
                    """,
                    bank_tx_id,
                    ctx["tenant_id"],
                    body.paid_through_id,
                    body.expense_date,
                    -total_amount,  # Negative = outflow
                    expense_id,
                    f"Expense: {expense_number}",
                    body.vendor_name or "Expense",
                    ctx["user_id"],
                    str(journal_id),
                )

                # Link attachments via document_attachments
                if body.attachment_ids:
                    for doc_id in body.attachment_ids[:5]:  # Max 5 attachments
                        # Verify document exists and belongs to tenant
                        doc_exists = await conn.fetchval(
                            """
                            SELECT id FROM documents
                            WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL
                        """,
                            str(doc_id),
                            ctx["tenant_id"],
                        )

                        if doc_exists:
                            # Link document to expense
                            await conn.execute(
                                """
                                INSERT INTO document_attachments (
                                    tenant_id, document_id, entity_type, entity_id,
                                    attachment_type, display_order, attached_by
                                ) VALUES ($1, $2, 'expense', $3, 'receipt', 0, $4)
                                ON CONFLICT (document_id, entity_type, entity_id) DO NOTHING
                            """,
                                ctx["tenant_id"],
                                str(doc_id),
                                str(expense_id),
                                str(ctx["user_id"]),
                            )

                # Fetch created expense with attachment count
                expense = await conn.fetchrow(
                    """
                    SELECT e.*,
                        (SELECT COUNT(*) FROM document_attachments WHERE entity_type = 'expense' AND entity_id = e.id) as attachment_count
                    FROM expenses e WHERE e.id = $1
                """,
                    str(expense_id),
                )

                result_data = dict(expense)

                # Fetch attachments with signed URLs
                attachments = await conn.fetch(
                    """
                    SELECT d.id, d.file_name, d.file_size, d.file_type as mime_type,
                           d.width, d.height, d.file_url as url, d.thumbnail_path as thumbnail_url,
                           d.uploaded_at
                    FROM document_attachments da
                    JOIN documents d ON da.document_id = d.id
                    WHERE da.entity_type = 'expense' AND da.entity_id = $1
                    ORDER BY da.display_order
                """,
                    str(expense_id),
                )

                result_data["attachments"] = [dict(a) for a in attachments]

                return {
                    "success": True,
                    "message": f"Expense {expense_number} created successfully",
                    "data": result_data,
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating expense: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create expense")


async def create_expense_journal(
    conn,
    tenant_id,
    expense_id,
    expense_number,
    expense_date,
    is_itemized,
    account_id,
    line_items,
    subtotal,
    tax_amount,
    pph_amount,
    paid_through_coa_id,
    tax_id,
    pph_type=None,
    pph_rate=None,
    vendor_id=None,
):
    """Create journal entry for expense."""

    # Generate journal number
    journal_number = f"JV-EXP-{expense_number}"

    # Total debit = subtotal + tax, total credit = same
    total_debit = subtotal + tax_amount
    total_credit = subtotal + tax_amount

    # Create journal header
    journal_id = await conn.fetchval(
        """
        INSERT INTO journal_entries (
            tenant_id, journal_number, journal_date, description,
            source_type, source_id, status,
            total_debit, total_credit
        ) VALUES ($1, $2, $3, $4, $5, $6, 'DRAFT', $7, $8)
        RETURNING id
    """,
        tenant_id,
        journal_number,
        expense_date,
        f"Expense: {expense_number}",
        "expense",
        str(expense_id),
        total_debit,
        total_credit,
    )

    line_number = 1

    # DEBIT: Expense account(s)
    if is_itemized and line_items:
        for item in line_items:
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5)
            """,
                str(journal_id),
                line_number,
                str(item.account_id),
                item.amount,
                item.notes or "Expense item",
            )
            line_number += 1
    else:
        if account_id:
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5)
            """,
                str(journal_id),
                line_number,
                str(account_id),
                subtotal,
                "Expense",
            )
            line_number += 1

    # DEBIT: PPN Masukan (if tax)
    if tax_amount > 0:
        ppn_masukan_id = await resolve_account_id(conn, tenant_id, "1-10800")

        if ppn_masukan_id:
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5)
            """,
                str(journal_id),
                line_number,
                str(ppn_masukan_id),
                tax_amount,
                "PPN Masukan",
            )
            line_number += 1

    # CREDIT: Kas/Bank (Law 4: total_amount = subtotal + tax - pph)
    cash_credit = subtotal + tax_amount - pph_amount
    await conn.execute(
        """
        INSERT INTO journal_lines (
            journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, $3, 0, $4, $5)
    """,
        str(journal_id),
        line_number,
        str(paid_through_coa_id),
        cash_credit,
        "Payment",
    )
    line_number += 1

    # CREDIT: Hutang PPh (if pph withheld)
    if pph_amount > 0:
        hutang_pph_id = await resolve_account_id(conn, tenant_id, "2-10300")

        if hutang_pph_id:
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, 0, $4, $5)
            """,
                str(journal_id),
                line_number,
                str(hutang_pph_id),
                pph_amount,
                "PPh dipotong",
            )

    # Law 20: DRAFT->POSTED after all lines inserted
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
        journal_id,
    )

    # Wave 2: Write document_tax_lines (PPN on expense)
    if tax_amount > 0 and tax_id:
        import uuid as _uuid

        # Get the PPN Masukan journal_line_id
        ppn_jl_id = await conn.fetchval(
            """
            SELECT id FROM journal_lines
            WHERE journal_id = $1 AND account_id = (
                SELECT id FROM chart_of_accounts WHERE tenant_id = $2 AND account_code = '1-10800' LIMIT 1
            ) LIMIT 1
            """,
            journal_id,
            tenant_id,
        )
        # Resolve coa_id from tax_codes
        tc_coa = await conn.fetchval(
            "SELECT coa_id FROM tax_codes WHERE id = $1::uuid",
            str(tax_id),
        )
        dpp_val = float(subtotal)
        await conn.execute(
            """
            INSERT INTO document_tax_lines (
                id, tenant_id, document_type, document_id,
                tax_code_id, direction, base_amount, tax_amount,
                coa_id, journal_line_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            _uuid.uuid4(),
            tenant_id,
            "EXPENSE",
            expense_id,
            _uuid.UUID(str(tax_id)),
            "input",
            dpp_val,
            float(tax_amount),
            tc_coa,
            ppn_jl_id,
        )

    # Wave 2: Write withholding_tax_records (PPh on expense)
    if pph_amount > 0 and pph_type:
        import uuid as _uuid2

        # Resolve PPh tax_code_id from tax_codes by type
        pph_tax_code_id = await conn.fetchval(
            """
            SELECT id FROM tax_codes
            WHERE tenant_id = $1 AND tax_type = $2 AND is_active = true
            LIMIT 1
            """,
            tenant_id,
            pph_type.lower().replace("_", ""),
        )
        if pph_tax_code_id:
            tax_period = (
                expense_date.strftime("%Y-%m")
                if hasattr(expense_date, "strftime")
                else str(expense_date)[:7]
            )
            await conn.execute(
                """
                INSERT INTO withholding_tax_records (
                    id, tenant_id, direction, tax_code_id,
                    document_type, document_id, journal_id,
                    vendor_id, tax_period,
                    base_amount, tax_amount, status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                _uuid2.uuid4(),
                tenant_id,
                "cut",
                pph_tax_code_id,
                "EXPENSE",
                expense_id,
                journal_id,
                _uuid2.UUID(str(vendor_id)) if vendor_id else None,
                tax_period,
                float(subtotal),
                float(pph_amount),
                "recorded",
            )

    return journal_id


# =============================================================================
# UPDATE EXPENSE
# =============================================================================
@router.patch("/{expense_id}", response_model=UpdateExpenseResponse)
async def update_expense(
    request: Request, expense_id: UUID, body: UpdateExpenseRequest
):
    """
    Update an expense.

    **Restrictions:**
    - Only draft expenses can be updated
    - Posted/void expenses cannot be modified
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check exists and status
            existing = await conn.fetchrow(
                """
                SELECT status FROM expenses
                WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            if not existing:
                raise HTTPException(status_code=404, detail="Expense not found")

            if existing["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft expenses can be updated"
                )

            # Build update query dynamically
            updates = []
            params = []
            param_idx = 1

            update_data = body.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                if value is not None:
                    updates.append(f"{field} = ${param_idx}")
                    if isinstance(value, UUID):
                        params.append(str(value))
                    else:
                        params.append(value)
                    param_idx += 1

            if updates:
                updates.append("updated_at = NOW()")
                params.extend([str(expense_id), ctx["tenant_id"]])

                query = f"""
                    UPDATE expenses
                    SET {', '.join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

            # Fetch updated expense
            expense = await conn.fetchrow(
                """
                SELECT * FROM expenses WHERE id = $1
            """,
                str(expense_id),
            )

            return {
                "success": True,
                "message": "Expense updated successfully",
                "data": dict(expense),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating expense {expense_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update expense")


# =============================================================================
# DELETE EXPENSE
# =============================================================================
@router.delete("/{expense_id}", response_model=DeleteExpenseResponse)
async def delete_expense(request: Request, expense_id: UUID):
    """
    Delete an expense.

    **Restrictions:**
    - Only draft expenses can be deleted
    - Use void endpoint for posted expenses
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check exists and status
            existing = await conn.fetchrow(
                """
                SELECT status, expense_number FROM expenses
                WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            if not existing:
                raise HTTPException(status_code=404, detail="Expense not found")

            if existing["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft expenses can be deleted. Use void for posted expenses.",
                )

            # Delete expense (cascade deletes items)
            await conn.execute(
                """
                DELETE FROM expenses WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "message": f"Expense {existing['expense_number']} deleted successfully",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting expense {expense_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete expense")


# =============================================================================
# VOID EXPENSE
# =============================================================================
@router.post("/{expense_id}/void", response_model=VoidExpenseResponse)
async def void_expense(request: Request, expense_id: UUID, body: VoidExpenseRequest):
    """
    Void a posted expense.

    - Voids the associated journal entry
    - Reason is required for audit trail
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            async with conn.transaction():
                # Law 13: Advisory lock to prevent concurrent void
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"EXPENSE_VOID:{expense_id}",
                )

                # Get expense
                expense = await conn.fetchrow(
                    """
                    SELECT * FROM expenses
                    WHERE id = $1 AND tenant_id = $2
                """,
                    str(expense_id),
                    ctx["tenant_id"],
                )

                if not expense:
                    raise HTTPException(status_code=404, detail="Expense not found")

                if expense["status"] == "void":
                    raise HTTPException(
                        status_code=400, detail="Expense already voided"
                    )

                # Law 5: Check accounting period is open
                await check_period_is_open(
                    conn, ctx["tenant_id"], expense["expense_date"]
                )

                # Void original journal + create reversal (Law 2)
                if expense["journal_id"]:
                    # Create reversal journal entry
                    original_lines = await conn.fetch(
                        """
                        SELECT account_id, debit, credit, memo
                        FROM journal_lines WHERE journal_id = $1
                        ORDER BY line_number
                        """,
                        expense["journal_id"],
                    )

                    if original_lines:
                        reversal_total = sum(r["debit"] or 0 for r in original_lines)
                        reversal_journal_id = await conn.fetchval(
                            """
                            INSERT INTO journal_entries (
                                tenant_id, journal_number, journal_date, description,
                                source_type, source_id, status,
                                total_debit, total_credit, reversal_of_id
                            ) VALUES ($1, $2, CURRENT_DATE, $3, 'expense_reversal', $4, 'DRAFT', $5, $6, $7)
                            RETURNING id
                            """,
                            ctx["tenant_id"],
                            "REV-" + expense["expense_number"],
                            "Reversal: "
                            + expense["expense_number"]
                            + " - "
                            + body.reason,
                            str(expense_id),
                            reversal_total,
                            reversal_total,
                            expense["journal_id"],
                        )

                        for line_idx, line in enumerate(original_lines, 1):
                            await conn.execute(
                                """
                                INSERT INTO journal_lines (
                                    journal_id, line_number, account_id, debit, credit, memo
                                ) VALUES ($1, $2, $3, $4, $5, $6)
                                """,
                                str(reversal_journal_id),
                                line_idx,
                                line["account_id"],
                                line["credit"] or 0,  # swap: original credit -> debit
                                line["debit"] or 0,  # swap: original debit -> credit
                                ("Reversal: " + (line["memo"] or "")),
                            )

                        # Law 20: DRAFT->POSTED after all reversal lines
                        await conn.execute(
                            "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                            reversal_journal_id,
                        )

                        # Law 26: Link original journal to reversal
                        await conn.execute(
                            """
                            UPDATE journal_entries
                            SET reversed_by_id = $2, status = 'VOID'
                            WHERE id = $1
                            """,
                            expense["journal_id"],
                            reversal_journal_id,
                        )

                        # BankSync Rule 3: Create mirror bank transaction for void
                        original_btxn = await conn.fetchrow(
                            """
                            SELECT * FROM bank_transactions
                            WHERE reference_type = 'expense'
                              AND reference_id = $1
                              AND tenant_id = $2
                              AND status != 'void'
                            """,
                            expense_id,
                            ctx["tenant_id"],
                        )
                        if original_btxn:
                            mirror_type = (
                                "deposit"
                                if original_btxn["transaction_type"] == "withdrawal"
                                else "withdrawal"
                            )
                            await conn.execute(
                                """
                                INSERT INTO bank_transactions (
                                    id, tenant_id, bank_account_id, transaction_date,
                                    transaction_type, amount, description,
                                    reference_type, reference_id, journal_id,
                                    status, origin_type, source_module,
                                    created_by, created_at
                                ) VALUES (
                                    gen_random_uuid(), $1, $2, CURRENT_DATE,
                                    $3, $4, $5,
                                    'expense', $6, $7,
                                    'POSTED', 'system', 'expense',
                                    $8, NOW()
                                )
                                """,
                                ctx["tenant_id"],
                                original_btxn["bank_account_id"],
                                mirror_type,
                                abs(original_btxn["amount"]),
                                f"[VOID] {original_btxn['description'] or ''}",
                                expense_id,
                                reversal_journal_id,
                                ctx.get("user_id"),
                            )
                            await conn.execute(
                                "UPDATE bank_transactions SET status = 'VOIDED', voided_at = NOW() WHERE id = $1",
                                original_btxn["id"],
                            )

                # Update expense status
                await conn.execute(
                    """
                    UPDATE expenses
                    SET status = 'void', updated_at = NOW(), notes = COALESCE(notes, '') || ' [VOID: ' || $3 || ']'
                    WHERE id = $1 AND tenant_id = $2
                """,
                    str(expense_id),
                    ctx["tenant_id"],
                    body.reason,
                )

                return {
                    "success": True,
                    "message": f"Expense {expense['expense_number']} voided successfully",
                    "data": {"id": str(expense_id), "status": "void"},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding expense {expense_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void expense")


# =============================================================================
# EXPENSE ATTACHMENTS
# =============================================================================


class AddAttachmentRequest(BaseModel):
    """Request to add attachment to expense."""

    document_id: UUID = Field(..., description="Document ID to attach")


class AttachmentResponse(BaseModel):
    """Response for attachment operations."""

    success: bool = True
    message: str
    data: Optional[Dict[str, Any]] = None


@router.get("/{expense_id}/attachments")
async def list_expense_attachments(request: Request, expense_id: UUID):
    """Get all attachments for an expense."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify expense exists
            expense = await conn.fetchval(
                """
                SELECT id FROM expenses WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            # Fetch attachments with document details
            attachments = await conn.fetch(
                """
                SELECT d.id, d.file_name, d.file_size, d.file_type as mime_type,
                       d.width, d.height, d.file_url as url, d.thumbnail_path as thumbnail_url,
                       d.uploaded_at, da.display_order
                FROM document_attachments da
                JOIN documents d ON da.document_id = d.id
                WHERE da.entity_type = 'expense' AND da.entity_id = $1
                AND d.deleted_at IS NULL
                ORDER BY da.display_order, d.uploaded_at
            """,
                str(expense_id),
            )

            return {"success": True, "data": [dict(a) for a in attachments]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching expense attachments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch attachments")


@router.post("/{expense_id}/attachments", response_model=AttachmentResponse)
async def add_expense_attachment(
    request: Request, expense_id: UUID, body: AddAttachmentRequest
):
    """Add an attachment to an existing expense."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify expense exists
            expense = await conn.fetchrow(
                """
                SELECT id, has_receipt FROM expenses WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            # Verify document exists
            document = await conn.fetchrow(
                """
                SELECT id, file_name FROM documents
                WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL
            """,
                str(body.document_id),
                ctx["tenant_id"],
            )

            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            # Check attachment limit (max 5)
            current_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM document_attachments
                WHERE entity_type = 'expense' AND entity_id = $1
            """,
                str(expense_id),
            )

            if current_count >= 5:
                raise HTTPException(
                    status_code=400, detail="Maximum 5 attachments per expense"
                )

            # Check if already attached
            existing = await conn.fetchval(
                """
                SELECT id FROM document_attachments
                WHERE document_id = $1 AND entity_type = 'expense' AND entity_id = $2
            """,
                str(body.document_id),
                str(expense_id),
            )

            if existing:
                raise HTTPException(
                    status_code=400, detail="Document already attached to this expense"
                )

            # Add attachment
            await conn.execute(
                """
                INSERT INTO document_attachments (
                    tenant_id, document_id, entity_type, entity_id,
                    attachment_type, display_order, attached_by
                ) VALUES ($1, $2, 'expense', $3, 'receipt', $4, $5)
            """,
                ctx["tenant_id"],
                str(body.document_id),
                str(expense_id),
                current_count,
                str(ctx["user_id"]),
            )

            # Update has_receipt flag
            if not expense["has_receipt"]:
                await conn.execute(
                    """
                    UPDATE expenses SET has_receipt = true, updated_at = NOW()
                    WHERE id = $1
                """,
                    str(expense_id),
                )

            return {
                "success": True,
                "message": f"Attachment {document['file_name']} added successfully",
                "data": {"document_id": str(body.document_id)},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding expense attachment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add attachment")


@router.delete(
    "/{expense_id}/attachments/{attachment_id}", response_model=AttachmentResponse
)
async def remove_expense_attachment(
    request: Request, expense_id: UUID, attachment_id: UUID
):
    """Remove an attachment from an expense."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify expense exists
            expense = await conn.fetchval(
                """
                SELECT id FROM expenses WHERE id = $1 AND tenant_id = $2
            """,
                str(expense_id),
                ctx["tenant_id"],
            )

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            # Delete attachment link (not the document itself)
            deleted = await conn.fetchval(
                """
                DELETE FROM document_attachments
                WHERE document_id = $1 AND entity_type = 'expense' AND entity_id = $2
                AND tenant_id = $3
                RETURNING id
            """,
                str(attachment_id),
                str(expense_id),
                ctx["tenant_id"],
            )

            if not deleted:
                raise HTTPException(status_code=404, detail="Attachment not found")

            # Check remaining attachments and update has_receipt
            remaining = await conn.fetchval(
                """
                SELECT COUNT(*) FROM document_attachments
                WHERE entity_type = 'expense' AND entity_id = $1
            """,
                str(expense_id),
            )

            if remaining == 0:
                await conn.execute(
                    """
                    UPDATE expenses SET has_receipt = false, updated_at = NOW()
                    WHERE id = $1
                """,
                    str(expense_id),
                )

            return {"success": True, "message": "Attachment removed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing expense attachment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove attachment")


@router.get("/{expense_id}/journal-entries")
async def get_expense_journal_entries(request: Request, expense_id: UUID):
    """
    Tab: Journal Entries - Get journal entries linked to this expense.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            expense = await conn.fetchrow(
                """
                SELECT id, expense_number, journal_id, status
                FROM expenses
                WHERE id = $1 AND tenant_id = $2
                """,
                expense_id,
                ctx["tenant_id"],
            )

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            journal_ids = set()
            if expense["journal_id"]:
                journal_ids.add(expense["journal_id"])

            source_journals = await conn.fetch(
                """
                SELECT id FROM journal_entries
                WHERE tenant_id = $1 AND source_id = $2
                """,
                ctx["tenant_id"],
                expense_id,
            )
            for row in source_journals:
                journal_ids.add(row["id"])

            if not journal_ids:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "summary": {
                        "total_debit": 0,
                        "total_credit": 0,
                        "is_balanced": True,
                    },
                }

            journals = await conn.fetch(
                """
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.status, je.total_debit, je.total_credit
                FROM journal_entries je
                WHERE je.id = ANY($1::uuid[])
                ORDER BY je.journal_date, je.created_at
                """,
                list(journal_ids),
            )

            journal_data = []
            total_debit = 0
            total_credit = 0

            for journal in journals:
                lines = await conn.fetch(
                    """
                    SELECT jl.id, jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo,
                           coa.account_code, coa.name as account_name
                    FROM journal_lines jl
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    journal["id"],
                )

                line_data = [
                    {
                        "id": str(line["id"]),
                        "line_number": line["line_number"],
                        "account_id": str(line["account_id"]),
                        "account_code": line["account_code"],
                        "account_name": line["account_name"],
                        "debit": str(line["debit"] or 0),
                        "credit": str(line["credit"] or 0),
                        "memo": line["memo"] or "",
                    }
                    for line in lines
                ]

                journal_debit = int(journal["total_debit"] or 0)  # Law 25: read path
                journal_credit = int(journal["total_credit"] or 0)  # Law 25: read path
                total_debit += journal_debit
                total_credit += journal_credit

                journal_data.append(
                    {
                        "id": str(journal["id"]),
                        "journal_number": journal["journal_number"],
                        "journal_date": journal["journal_date"].isoformat()
                        if journal["journal_date"]
                        else None,
                        "description": journal["description"],
                        "source_type": journal["source_type"],
                        "status": journal["status"],
                        "total_debit": journal_debit,
                        "total_credit": journal_credit,
                        "is_balanced": abs(journal_debit - journal_credit) < 0.01,
                        "lines": line_data,
                    }
                )

            return {
                "success": True,
                "data": journal_data,
                "total": len(journal_data),
                "summary": {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "is_balanced": abs(total_debit - total_credit) < 0.01,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting expense journal entries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")
