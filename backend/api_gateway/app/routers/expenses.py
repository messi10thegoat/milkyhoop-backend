"""
Expenses Router - Biaya & Pengeluaran Management

Endpoints for managing expenses with auto journal posting.
Supports single and itemized expenses with PPh withholding.
"""

from fastapi import APIRouter, HTTPException, Request, Query
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


# =============================================================================
# HEALTH CHECK
# =============================================================================
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
    )
):
    """
    Get expense summary statistics for dashboard.

    Returns total amounts, counts, and top expense accounts.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Date filter based on period
            date_filter = {
                "week": "expense_date >= CURRENT_DATE - INTERVAL '7 days'",
                "month": "expense_date >= DATE_TRUNC('month', CURRENT_DATE)",
                "quarter": "expense_date >= DATE_TRUNC('quarter', CURRENT_DATE)",
                "year": "expense_date >= DATE_TRUNC('year', CURRENT_DATE)"
            }[period]

            # Main summary query
            summary = await conn.fetchrow(f"""
                SELECT
                    COUNT(*) as total_count,
                    COALESCE(SUM(total_amount), 0) as total_amount,
                    COALESCE(SUM(tax_amount), 0) as total_tax,
                    COUNT(DISTINCT vendor_id) as vendor_count,
                    COUNT(CASE WHEN is_billable THEN 1 END) as billable_count,
                    COALESCE(SUM(CASE WHEN is_billable THEN total_amount END), 0) as billable_amount
                FROM expenses
                WHERE tenant_id = $1 AND status = 'posted' AND {date_filter}
            """, ctx["tenant_id"])

            # Top expense accounts
            top_accounts = await conn.fetch(f"""
                SELECT
                    account_id, account_name,
                    SUM(amount) as total_amount,
                    COUNT(*) as count
                FROM (
                    -- Non-itemized expenses
                    SELECT account_id, account_name, total_amount as amount
                    FROM expenses
                    WHERE tenant_id = $1 AND status = 'posted'
                      AND NOT is_itemized AND {date_filter}
                    UNION ALL
                    -- Itemized expense items
                    SELECT ei.account_id, ei.account_name, ei.amount
                    FROM expense_items ei
                    JOIN expenses e ON ei.expense_id = e.id
                    WHERE e.tenant_id = $1 AND e.status = 'posted' AND {date_filter}
                ) combined
                WHERE account_id IS NOT NULL
                GROUP BY account_id, account_name
                ORDER BY total_amount DESC
                LIMIT 5
            """, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "period": period,
                    "total_count": summary["total_count"],
                    "total_amount": summary["total_amount"],
                    "total_tax": summary["total_tax"],
                    "vendor_count": summary["vendor_count"],
                    "billable_count": summary["billable_count"],
                    "billable_amount": summary["billable_amount"],
                    "top_accounts": [
                        {
                            "account_id": r["account_id"],
                            "account_name": r["account_name"],
                            "total_amount": r["total_amount"],
                            "count": r["count"]
                        }
                        for r in top_accounts
                    ]
                }
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
async def calculate_expense_totals(
    request: Request,
    body: CreateExpenseRequest
):
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
        tax_amount = int(subtotal * float(body.tax_rate or 0) / 100)
        pph_amount = int(subtotal * float(body.pph_rate or 0) / 100)
        total_amount = subtotal + tax_amount - pph_amount

        return {
            "success": True,
            "calculation": {
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "pph_amount": pph_amount,
                "total_amount": total_amount
            }
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
    search: Optional[str] = Query(None, description="Search expense number, vendor, or notes"),
    sort_by: Literal["expense_date", "created_at", "total_amount"] = Query(
        "expense_date", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
    is_billable: Optional[bool] = Query(None, description="Filter by billable status")
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

            if search:
                conditions.append(f"""
                    (expense_number ILIKE ${param_idx}
                     OR vendor_name ILIKE ${param_idx}
                     OR notes ILIKE ${param_idx})
                """)
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Validate sort_by to prevent SQL injection
            valid_sort = {"expense_date": "expense_date", "created_at": "created_at", "total_amount": "total_amount"}
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
                items.append({
                    "id": row["id"],
                    "expense_number": row["expense_number"],
                    "expense_date": row["expense_date"],
                    "paid_through_name": row["paid_through_name"],
                    "vendor": {
                        "id": row["vendor_id"],
                        "name": row["vendor_name"]
                    } if row["vendor_id"] else None,
                    "account_name": row["account_name"],
                    "subtotal": row["subtotal"],
                    "tax_amount": row["tax_amount"],
                    "total_amount": row["total_amount"],
                    "is_itemized": row["is_itemized"] or False,
                    "status": row["status"],
                    "is_billable": row["is_billable"] or False,
                    "has_receipt": row["has_receipt"] or False,
                    "reference": row["reference"],
                    "notes": row["notes"],
                    "created_at": row["created_at"]
                })

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

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
    limit: int = Query(10, ge=1, le=50, description="Max results")
):
    """Fast search for expense autocomplete in forms."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            rows = await conn.fetch("""
                SELECT id, expense_number, expense_date, total_amount, vendor_name
                FROM expenses
                WHERE tenant_id = $1
                  AND status != 'void'
                  AND (expense_number ILIKE $2 OR vendor_name ILIKE $2)
                ORDER BY expense_date DESC
                LIMIT $3
            """, ctx["tenant_id"], f"%{q}%", limit)

            return {
                "items": [
                    {
                        "id": row["id"],
                        "expense_number": row["expense_number"],
                        "expense_date": row["expense_date"],
                        "total_amount": row["total_amount"],
                        "vendor_name": row["vendor_name"]
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
# GET EXPENSE DETAIL
# =============================================================================
@router.get("/{expense_id}", response_model=ExpenseDetailResponse)
async def get_expense_detail(
    request: Request,
    expense_id: UUID
):
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
            expense = await conn.fetchrow("""
                SELECT * FROM expenses
                WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            result = dict(expense)

            # Fetch items if itemized
            if expense["is_itemized"]:
                items = await conn.fetch("""
                    SELECT id, account_id, account_name, amount, notes, line_number
                    FROM expense_items
                    WHERE expense_id = $1
                    ORDER BY line_number
                """, str(expense_id))
                result["items"] = [dict(i) for i in items]
            else:
                result["items"] = []

            # Fetch attachments
            attachments = await conn.fetch("""
                SELECT id, file_name, file_path, file_size, mime_type, uploaded_at
                FROM expense_attachments
                WHERE expense_id = $1
            """, str(expense_id))
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
async def create_expense(
    request: Request,
    body: CreateExpenseRequest
):
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
                # Generate expense number
                expense_number = await conn.fetchval(
                    "SELECT generate_expense_number($1)",
                    ctx["tenant_id"]
                )

                # Calculate totals
                if body.is_itemized and body.line_items:
                    subtotal = sum(item.amount for item in body.line_items)
                else:
                    subtotal = body.amount or 0

                tax_amount = int(subtotal * float(body.tax_rate or 0) / 100)
                pph_amount = int(subtotal * float(body.pph_rate or 0) / 100)
                total_amount = subtotal + tax_amount - pph_amount

                # Get paid_through account info
                paid_through = await conn.fetchrow("""
                    SELECT ba.id, ba.account_name, ba.coa_id, coa.name as coa_name
                    FROM bank_accounts ba
                    LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
                    WHERE ba.id = $1 AND ba.tenant_id = $2
                """, str(body.paid_through_id), ctx["tenant_id"])

                if not paid_through:
                    raise HTTPException(status_code=400, detail="Invalid paid_through account")

                # Auto-set has_receipt if attachments provided
                has_receipt = body.has_receipt or (body.attachment_ids and len(body.attachment_ids) > 0)

                # Insert expense
                expense_id = await conn.fetchval("""
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
                    ctx["tenant_id"], expense_number, body.expense_date,
                    str(body.paid_through_id), paid_through["account_name"], paid_through["coa_id"],
                    str(body.vendor_id) if body.vendor_id else None, body.vendor_name,
                    str(body.account_id) if body.account_id else None, body.account_name,
                    body.currency or "IDR", subtotal,
                    str(body.tax_id) if body.tax_id else None, body.tax_name,
                    float(body.tax_rate or 0), tax_amount,
                    body.pph_type, float(body.pph_rate or 0), pph_amount,
                    total_amount, body.is_itemized, "posted",
                    body.is_billable, str(body.billed_to_customer_id) if body.billed_to_customer_id else None,
                    body.reference, body.notes, has_receipt,
                    str(ctx["user_id"])
                )

                # Insert line items if itemized
                if body.is_itemized and body.line_items:
                    for idx, item in enumerate(body.line_items, 1):
                        await conn.execute("""
                            INSERT INTO expense_items (
                                tenant_id, expense_id, account_id, account_name,
                                amount, notes, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            ctx["tenant_id"], str(expense_id),
                            str(item.account_id), item.account_name,
                            item.amount, item.notes, idx
                        )

                # Create journal entry
                journal_id = await create_expense_journal(
                    conn, ctx["tenant_id"], expense_id, expense_number,
                    body.expense_date, body.is_itemized,
                    body.account_id, body.line_items,
                    subtotal, tax_amount, pph_amount,
                    paid_through["coa_id"], body.tax_id
                )

                # Update expense with journal_id
                await conn.execute("""
                    UPDATE expenses SET journal_id = $1 WHERE id = $2
                """, str(journal_id), str(expense_id))

                # Link attachments via document_attachments
                if body.attachment_ids:
                    for doc_id in body.attachment_ids[:5]:  # Max 5 attachments
                        # Verify document exists and belongs to tenant
                        doc_exists = await conn.fetchval("""
                            SELECT id FROM documents
                            WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL
                        """, str(doc_id), ctx["tenant_id"])

                        if doc_exists:
                            # Link document to expense
                            await conn.execute("""
                                INSERT INTO document_attachments (
                                    tenant_id, document_id, entity_type, entity_id,
                                    attachment_type, display_order, attached_by
                                ) VALUES ($1, $2, 'expense', $3, 'receipt', 0, $4)
                                ON CONFLICT (document_id, entity_type, entity_id) DO NOTHING
                            """,
                                ctx["tenant_id"], str(doc_id), str(expense_id), str(ctx["user_id"])
                            )

                # Fetch created expense with attachment count
                expense = await conn.fetchrow("""
                    SELECT e.*,
                        (SELECT COUNT(*) FROM document_attachments WHERE entity_type = 'expense' AND entity_id = e.id) as attachment_count
                    FROM expenses e WHERE e.id = $1
                """, str(expense_id))

                result_data = dict(expense)

                # Fetch attachments with signed URLs
                attachments = await conn.fetch("""
                    SELECT d.id, d.file_name, d.file_size, d.file_type as mime_type,
                           d.width, d.height, d.file_url as url, d.thumbnail_path as thumbnail_url,
                           d.uploaded_at
                    FROM document_attachments da
                    JOIN documents d ON da.document_id = d.id
                    WHERE da.entity_type = 'expense' AND da.entity_id = $1
                    ORDER BY da.display_order
                """, str(expense_id))

                result_data["attachments"] = [dict(a) for a in attachments]

                return {
                    "success": True,
                    "message": f"Expense {expense_number} created successfully",
                    "data": result_data
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating expense: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create expense")


async def create_expense_journal(
    conn, tenant_id, expense_id, expense_number,
    expense_date, is_itemized, account_id, line_items,
    subtotal, tax_amount, pph_amount, paid_through_coa_id, tax_id
):
    """Create journal entry for expense."""

    # Generate journal number
    journal_number = f"JV-EXP-{expense_number}"

    # Total debit = subtotal + tax, total credit = same
    total_debit = subtotal + tax_amount
    total_credit = subtotal + tax_amount

    # Create journal header
    journal_id = await conn.fetchval("""
        INSERT INTO journal_entries (
            tenant_id, journal_number, journal_date, description,
            source_type, source_id, status,
            total_debit, total_credit
        ) VALUES ($1, $2, $3, $4, $5, $6, 'POSTED', $7, $8)
        RETURNING id
    """,
        tenant_id, journal_number, expense_date,
        f"Expense: {expense_number}",
        "expense", str(expense_id),
        total_debit, total_credit
    )

    line_number = 1

    # DEBIT: Expense account(s)
    if is_itemized and line_items:
        for item in line_items:
            await conn.execute("""
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5)
            """,
                str(journal_id), line_number,
                str(item.account_id), item.amount,
                item.notes or "Expense item"
            )
            line_number += 1
    else:
        if account_id:
            await conn.execute("""
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5)
            """,
                str(journal_id), line_number,
                str(account_id), subtotal, "Expense"
            )
            line_number += 1

    # DEBIT: PPN Masukan (if tax)
    if tax_amount > 0:
        ppn_masukan_id = await conn.fetchval("""
            SELECT id FROM chart_of_accounts
            WHERE tenant_id = $1 AND account_code = '1-10700'
        """, tenant_id)

        if ppn_masukan_id:
            await conn.execute("""
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5)
            """,
                str(journal_id), line_number,
                str(ppn_masukan_id), tax_amount, "PPN Masukan"
            )
            line_number += 1

    # CREDIT: Kas/Bank
    await conn.execute("""
        INSERT INTO journal_lines (
            journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, $3, 0, $4, $5)
    """,
        str(journal_id), line_number,
        str(paid_through_coa_id), subtotal + tax_amount, "Payment"
    )
    line_number += 1

    # CREDIT: Hutang PPh (if pph withheld)
    if pph_amount > 0:
        hutang_pph_id = await conn.fetchval("""
            SELECT id FROM chart_of_accounts
            WHERE tenant_id = $1 AND account_code = '2-10500'
        """, tenant_id)

        if hutang_pph_id:
            await conn.execute("""
                INSERT INTO journal_lines (
                    journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, 0, $4, $5)
            """,
                str(journal_id), line_number,
                str(hutang_pph_id), pph_amount, "PPh dipotong"
            )

    return journal_id


# =============================================================================
# UPDATE EXPENSE
# =============================================================================
@router.patch("/{expense_id}", response_model=UpdateExpenseResponse)
async def update_expense(
    request: Request,
    expense_id: UUID,
    body: UpdateExpenseRequest
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
            existing = await conn.fetchrow("""
                SELECT status FROM expenses
                WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            if not existing:
                raise HTTPException(status_code=404, detail="Expense not found")

            if existing["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft expenses can be updated"
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
            expense = await conn.fetchrow("""
                SELECT * FROM expenses WHERE id = $1
            """, str(expense_id))

            return {
                "success": True,
                "message": "Expense updated successfully",
                "data": dict(expense)
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
async def delete_expense(
    request: Request,
    expense_id: UUID
):
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
            existing = await conn.fetchrow("""
                SELECT status, expense_number FROM expenses
                WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            if not existing:
                raise HTTPException(status_code=404, detail="Expense not found")

            if existing["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft expenses can be deleted. Use void for posted expenses."
                )

            # Delete expense (cascade deletes items)
            await conn.execute("""
                DELETE FROM expenses WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            return {
                "success": True,
                "message": f"Expense {existing['expense_number']} deleted successfully"
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
async def void_expense(
    request: Request,
    expense_id: UUID,
    body: VoidExpenseRequest
):
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
                # Get expense
                expense = await conn.fetchrow("""
                    SELECT * FROM expenses
                    WHERE id = $1 AND tenant_id = $2
                """, str(expense_id), ctx["tenant_id"])

                if not expense:
                    raise HTTPException(status_code=404, detail="Expense not found")

                if expense["status"] == "void":
                    raise HTTPException(status_code=400, detail="Expense already voided")

                # Void original journal
                if expense["journal_id"]:
                    await conn.execute("""
                        UPDATE journal_entries
                        SET status = 'VOID'
                        WHERE id = $1
                    """, expense["journal_id"])

                # Update expense status
                await conn.execute("""
                    UPDATE expenses
                    SET status = 'void', updated_at = NOW(), notes = COALESCE(notes, '') || ' [VOID: ' || $3 || ']'
                    WHERE id = $1 AND tenant_id = $2
                """, str(expense_id), ctx["tenant_id"], body.reason)

                return {
                    "success": True,
                    "message": f"Expense {expense['expense_number']} voided successfully",
                    "data": {"id": str(expense_id), "status": "void"}
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
async def list_expense_attachments(
    request: Request,
    expense_id: UUID
):
    """Get all attachments for an expense."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify expense exists
            expense = await conn.fetchval("""
                SELECT id FROM expenses WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            # Fetch attachments with document details
            attachments = await conn.fetch("""
                SELECT d.id, d.file_name, d.file_size, d.file_type as mime_type,
                       d.width, d.height, d.file_url as url, d.thumbnail_path as thumbnail_url,
                       d.uploaded_at, da.display_order
                FROM document_attachments da
                JOIN documents d ON da.document_id = d.id
                WHERE da.entity_type = 'expense' AND da.entity_id = $1
                AND d.deleted_at IS NULL
                ORDER BY da.display_order, d.uploaded_at
            """, str(expense_id))

            return {
                "success": True,
                "data": [dict(a) for a in attachments]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching expense attachments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch attachments")


@router.post("/{expense_id}/attachments", response_model=AttachmentResponse)
async def add_expense_attachment(
    request: Request,
    expense_id: UUID,
    body: AddAttachmentRequest
):
    """Add an attachment to an existing expense."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify expense exists
            expense = await conn.fetchrow("""
                SELECT id, has_receipt FROM expenses WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            # Verify document exists
            document = await conn.fetchrow("""
                SELECT id, file_name FROM documents
                WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL
            """, str(body.document_id), ctx["tenant_id"])

            if not document:
                raise HTTPException(status_code=404, detail="Document not found")

            # Check attachment limit (max 5)
            current_count = await conn.fetchval("""
                SELECT COUNT(*) FROM document_attachments
                WHERE entity_type = 'expense' AND entity_id = $1
            """, str(expense_id))

            if current_count >= 5:
                raise HTTPException(
                    status_code=400,
                    detail="Maximum 5 attachments per expense"
                )

            # Check if already attached
            existing = await conn.fetchval("""
                SELECT id FROM document_attachments
                WHERE document_id = $1 AND entity_type = 'expense' AND entity_id = $2
            """, str(body.document_id), str(expense_id))

            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="Document already attached to this expense"
                )

            # Add attachment
            await conn.execute("""
                INSERT INTO document_attachments (
                    tenant_id, document_id, entity_type, entity_id,
                    attachment_type, display_order, attached_by
                ) VALUES ($1, $2, 'expense', $3, 'receipt', $4, $5)
            """,
                ctx["tenant_id"], str(body.document_id), str(expense_id),
                current_count, str(ctx["user_id"])
            )

            # Update has_receipt flag
            if not expense["has_receipt"]:
                await conn.execute("""
                    UPDATE expenses SET has_receipt = true, updated_at = NOW()
                    WHERE id = $1
                """, str(expense_id))

            return {
                "success": True,
                "message": f"Attachment {document['file_name']} added successfully",
                "data": {"document_id": str(body.document_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding expense attachment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add attachment")


@router.delete("/{expense_id}/attachments/{attachment_id}", response_model=AttachmentResponse)
async def remove_expense_attachment(
    request: Request,
    expense_id: UUID,
    attachment_id: UUID
):
    """Remove an attachment from an expense."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify expense exists
            expense = await conn.fetchval("""
                SELECT id FROM expenses WHERE id = $1 AND tenant_id = $2
            """, str(expense_id), ctx["tenant_id"])

            if not expense:
                raise HTTPException(status_code=404, detail="Expense not found")

            # Delete attachment link (not the document itself)
            deleted = await conn.fetchval("""
                DELETE FROM document_attachments
                WHERE document_id = $1 AND entity_type = 'expense' AND entity_id = $2
                AND tenant_id = $3
                RETURNING id
            """, str(attachment_id), str(expense_id), ctx["tenant_id"])

            if not deleted:
                raise HTTPException(status_code=404, detail="Attachment not found")

            # Check remaining attachments and update has_receipt
            remaining = await conn.fetchval("""
                SELECT COUNT(*) FROM document_attachments
                WHERE entity_type = 'expense' AND entity_id = $1
            """, str(expense_id))

            if remaining == 0:
                await conn.execute("""
                    UPDATE expenses SET has_receipt = false, updated_at = NOW()
                    WHERE id = $1
                """, str(expense_id))

            return {
                "success": True,
                "message": "Attachment removed successfully"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing expense attachment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove attachment")
