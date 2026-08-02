"""
Credit Notes Router - Sales Returns and AR Adjustments

Endpoints for managing credit notes (nota kredit).
Credit notes reduce Accounts Receivable and can be applied to invoices or refunded.

Flow:
1. Create draft credit note
2. Post to accounting (creates AR reduction journal)
3. Apply to invoice(s) OR issue refund
4. Void if needed (only if unapplied)

Endpoints:
- GET    /credit-notes              - List credit notes
- GET    /credit-notes/summary      - Summary statistics
- GET    /credit-notes/{id}         - Get credit note detail
- POST   /credit-notes              - Create draft credit note
- PATCH  /credit-notes/{id}         - Update draft credit note
- DELETE /credit-notes/{id}         - Delete draft credit note
- POST   /credit-notes/{id}/post    - Post to accounting
- POST   /credit-notes/{id}/apply   - Apply to invoice(s)
- POST   /credit-notes/{id}/refund  - Issue cash refund
- POST   /credit-notes/{id}/void    - Void credit note
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..schemas.credit_notes import (
    CreateCreditNoteRequest,
    UpdateCreditNoteRequest,
    ApplyCreditNoteRequest,
    RefundCreditNoteRequest,
    VoidCreditNoteRequest,
    CreditNoteResponse,
    CreditNoteDetailResponse,
    CreditNoteListResponse,
    CreditNoteSummaryResponse,
)
from ..services.resolve_account import resolve_account_id
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,
)
from ..services.role_precondition import assert_required_roles_for_path

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool

# Account codes (resolved dynamically via Law 27)
AR_ACCOUNT_CODE = "1-10400"  # Piutang Usaha
SALES_RETURN_ACCOUNT_CODE = "4-10300"  # Retur Penjualan
# Fase D2-wrap C4: VAT keluaran (PPN Output) di credit note (retur penjualan)
# di-resolve via role VAT_OUTPUT dengan PKP guard. Const legacy
# TAX_PAYABLE_ACCOUNT_CODE = "2-10300" dihapus karena 2-10300 sekarang generic
# placeholder pasca D1 V155 repoint (VAT_OUTPUT pindah ke 2-10600 dedicated).

# Required role mappings for credit_notes posting path.
# VAT_OUTPUT is PKP-gated (tenant non-PKP tidak bisa post PPN > 0).
CREDIT_NOTES_REQUIRED_ROLES = [
    AccountRole.VAT_OUTPUT,
]

# One-time precondition check flag.
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition for credit_notes.

    Scopes the audit to the ACTING tenant when tenant_id is supplied (cached
    per-tenant); tenant_id=None preserves the legacy all-tenants behavior.
    Fails loud (PreconditionFailedError) if the audited tenant lacks any
    required role mapping.
    """
    if tenant_id is None:
        await assert_required_roles_for_path(
            pool, "credit_notes", CREDIT_NOTES_REQUIRED_ROLES
        )
        return
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "credit_notes", CREDIT_NOTES_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


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


def calculate_item_totals(item: dict) -> dict:
    """Calculate item totals with discount and tax."""
    quantity = Decimal(str(item.get("quantity", 0)))
    unit_price = Decimal(str(item.get("unit_price", 0)))
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    discount_amount = Decimal(str(item.get("discount_amount", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

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
        "subtotal": int(subtotal),
        "discount_amount": int(discount),
        "tax_amount": int(tax_amount),
        "total": int(total),
    }


async def get_invoice_remaining_from_journal(conn, tenant_id: str, invoice_id) -> int:
    """Compute invoice remaining from journal lines on AR account (Law 16).

    For sales invoices:
    - Invoice posting: DEBIT AR (1-10400) -> increases receivable
    - Payment received: CREDIT AR -> decreases receivable
    - Credit note applied: CREDIT AR -> decreases receivable
    - Customer deposit applied: CREDIT AR -> decreases receivable

    Outstanding = SUM(debit) - SUM(credit) on AR for this invoice's journal chain
    """
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.status = 'POSTED'
            AND coa.account_code = '1-10400'  -- Law 27: read filter, resolved via JOIN
            AND je.tenant_id = $1
            AND (
                -- Original invoice journal
                (je.source_type = 'INVOICE' AND je.source_id = $2)
                -- Payment journals linked via allocations
                OR (je.source_type IN ('RECEIVE_PAYMENT', 'PAYMENT_RECEIVED') AND EXISTS (
                    SELECT 1 FROM receive_payment_allocations rpa
                    WHERE rpa.invoice_id = $2 AND rpa.payment_id = je.source_id
                ))
                -- PAYMENT_RECEIVED orphan journals (description-based fallback)
                OR (je.source_type = 'PAYMENT_RECEIVED'
                    AND je.description LIKE '%%' || (SELECT invoice_number FROM sales_invoices WHERE id = $2) || '%%'
                    AND NOT EXISTS(
                        SELECT 1 FROM receive_payment_allocations rpa2
                        WHERE rpa2.payment_id = je.source_id AND rpa2.tenant_id = $1
                    ))
                -- Credit note journals linked via allocations
                OR (je.source_type = 'CREDIT_NOTE' AND EXISTS (
                    SELECT 1 FROM credit_note_applications cna
                    WHERE cna.invoice_id = $2 AND cna.credit_note_id = je.source_id
                ))
                -- Customer deposit application journals linked via allocations
                OR (je.source_type = 'DEPOSIT_APPLICATION' AND EXISTS (
                    SELECT 1 FROM customer_deposit_applications cda
                    WHERE cda.invoice_id = $2 AND cda.deposit_id = je.source_id
                    -- FIX_P1_DEPOSIT 2026-06-16 OPTION B: drop reversed (un-applied)
                    -- deposit applications so invoice outstanding is restored.
                    AND is_effective_journal(je.id)
                ))
                -- Invoice reversal (partial void)
                OR (je.source_type = 'INVOICE_REVERSAL' AND je.source_id = $2)
                -- Inline payments from sales_invoices.py
                OR (je.id IN (
                    SELECT sip.journal_id FROM sales_invoice_payments sip
                    WHERE sip.invoice_id = $2
                ))
            )
    """,
        tenant_id,
        invoice_id,
    )
    return int(result or 0)


# =============================================================================
# LIST CREDIT NOTES
# =============================================================================


@router.get("", response_model=CreditNoteListResponse)
async def list_credit_notes(
    request: Request,
    status: Optional[
        Literal["all", "draft", "posted", "partial", "applied", "void"]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(
        None, description="Search by number or customer name"
    ),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal[
        "credit_note_date", "credit_note_number", "total_amount", "created_at"
    ] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List credit notes with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(customer_id)  # BATCH1: credit_notes.customer_id is VARCHAR -> bind str, not UUID(...)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(credit_note_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(credit_note_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")
                param_idx += 1

            if date_from:
                conditions.append(f"credit_note_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"credit_note_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "credit_note_date": "credit_note_date",
                "credit_note_number": "credit_note_number",
                "total_amount": "total_amount",
                "created_at": "created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM credit_notes WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, credit_note_number, customer_id, customer_name,
                       credit_note_date, total_amount, amount_applied, amount_refunded,
                       status, reason, created_at
                FROM credit_notes
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "credit_note_number": row["credit_note_number"],
                    "customer_id": str(row["customer_id"])
                    if row["customer_id"]
                    else None,
                    "customer_name": row["customer_name"],
                    "credit_note_date": row["credit_note_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["total_amount"]
                    - (row["amount_applied"] or 0)
                    - (row["amount_refunded"] or 0),
                    "status": row["status"],
                    "reason": row["reason"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing credit notes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list credit notes")


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=CreditNoteSummaryResponse)
async def get_credit_notes_summary(request: Request):
    """Get summary statistics for credit notes."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Law 16: Counts from table, amounts from journal (truth)
            query = """
                WITH cn_journal AS (
                    SELECT cn.id,
                           COALESCE(SUM(CASE WHEN coa.account_type = 'RECEIVABLE' AND jl.credit > 0 THEN jl.credit ELSE 0 END), 0) as journal_value
                    FROM credit_notes cn
                    JOIN journal_entries je ON je.source_id = cn.id AND je.source_type = 'CREDIT_NOTE'
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE cn.tenant_id = $1 AND cn.status != 'void'
                      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                    GROUP BY cn.id
                )
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE cn.status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE cn.status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE cn.status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE cn.status = 'applied') as applied_count,
                    COALESCE(SUM(cnj.journal_value), 0) as total_value,
                    COALESCE(SUM(cn.amount_applied), 0) as total_applied,
                    COALESCE(SUM(cn.amount_refunded), 0) as total_refunded,
                    COALESCE(SUM(cnj.journal_value - COALESCE(cn.amount_applied, 0) - COALESCE(cn.amount_refunded, 0))
                        FILTER (WHERE cn.status IN ('posted', 'partial')), 0) as available_balance
                FROM credit_notes cn
                LEFT JOIN cn_journal cnj ON cnj.id = cn.id
                WHERE cn.tenant_id = $1 AND cn.status != 'void'
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "partial_count": row["partial_count"] or 0,
                    "applied_count": row["applied_count"] or 0,
                    "total_value": int(row["total_value"] or 0),
                    "total_applied": int(row["total_applied"] or 0),
                    "total_refunded": int(row["total_refunded"] or 0),
                    "available_balance": int(row["available_balance"] or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credit notes summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET CREDIT NOTE DETAIL
# =============================================================================


@router.get("/{credit_note_id}", response_model=CreditNoteDetailResponse)
async def get_credit_note(request: Request, credit_note_id: UUID):
    """Get detailed information for a credit note."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get credit note
            cn = await conn.fetchrow(
                """
                SELECT * FROM credit_notes
                WHERE id = $1 AND tenant_id = $2
            """,
                credit_note_id,
                ctx["tenant_id"],
            )

            if not cn:
                raise HTTPException(status_code=404, detail="Credit note not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM credit_note_items
                WHERE credit_note_id = $1
                ORDER BY line_number
            """,
                credit_note_id,
            )

            # Get applications with invoice numbers
            applications = await conn.fetch(
                """
                SELECT a.*, i.invoice_number
                FROM credit_note_applications a
                LEFT JOIN sales_invoices i ON a.invoice_id = i.id
                WHERE a.credit_note_id = $1
                ORDER BY a.application_date
            """,
                credit_note_id,
            )

            # Get refunds
            refunds = await conn.fetch(
                """
                SELECT * FROM credit_note_refunds
                WHERE credit_note_id = $1
                ORDER BY refund_date
            """,
                credit_note_id,
            )

            # Build response
            remaining = (
                cn["total_amount"]
                - (cn["amount_applied"] or 0)
                - (cn["amount_refunded"] or 0)
            )

            return {
                "success": True,
                "data": {
                    "id": str(cn["id"]),
                    "credit_note_number": cn["credit_note_number"],
                    "customer_id": str(cn["customer_id"])
                    if cn["customer_id"]
                    else None,
                    "customer_name": cn["customer_name"],
                    "original_invoice_id": str(cn["original_invoice_id"])
                    if cn["original_invoice_id"]
                    else None,
                    "original_invoice_number": cn["original_invoice_number"],
                    "subtotal": cn["subtotal"],
                    "discount_percent": float(cn["discount_percent"] or 0),
                    "discount_amount": cn["discount_amount"] or 0,
                    "tax_rate": float(cn["tax_rate"] or 0),
                    "tax_amount": cn["tax_amount"] or 0,
                    "total_amount": cn["total_amount"],
                    "amount_applied": cn["amount_applied"] or 0,
                    "amount_refunded": cn["amount_refunded"] or 0,
                    "remaining_amount": remaining,
                    "status": cn["status"],
                    "credit_note_date": cn["credit_note_date"].isoformat(),
                    "reason": cn["reason"],
                    "reason_detail": cn["reason_detail"],
                    "ref_no": cn["ref_no"],
                    "notes": cn["notes"],
                    "ar_id": str(cn["ar_id"]) if cn["ar_id"] else None,
                    "journal_id": str(cn["journal_id"]) if cn["journal_id"] else None,
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
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
                    "applications": [
                        {
                            "id": str(app["id"]),
                            "invoice_id": str(app["invoice_id"]),
                            "invoice_number": app["invoice_number"],
                            "amount_applied": app["amount_applied"],
                            "application_date": app["application_date"].isoformat(),
                            "created_at": app["created_at"].isoformat(),
                        }
                        for app in applications
                    ],
                    "refunds": [
                        {
                            "id": str(ref["id"]),
                            "amount": ref["amount"],
                            "refund_date": ref["refund_date"].isoformat(),
                            "payment_method": ref["payment_method"],
                            "account_id": str(ref["account_id"]),
                            "reference": ref["reference"],
                            "created_at": ref["created_at"].isoformat(),
                        }
                        for ref in refunds
                    ],
                    "posted_at": cn["posted_at"].isoformat()
                    if cn["posted_at"]
                    else None,
                    "posted_by": str(cn["posted_by"]) if cn["posted_by"] else None,
                    "voided_at": cn["voided_at"].isoformat()
                    if cn["voided_at"]
                    else None,
                    "voided_reason": cn["voided_reason"],
                    "created_at": cn["created_at"].isoformat(),
                    "updated_at": cn["updated_at"].isoformat(),
                    "created_by": str(cn["created_by"]) if cn["created_by"] else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get credit note")


# =============================================================================
# CREATE CREDIT NOTE (DRAFT)
# =============================================================================


@router.post("", response_model=CreditNoteResponse, status_code=201)
async def create_credit_note(request: Request, body: CreateCreditNoteRequest):
    """
    Create a new credit note in draft status.

    Draft credit notes can be edited before posting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate credit note number
                cn_number = await conn.fetchval(
                    "SELECT generate_credit_note_number($1, 'CN')", ctx["tenant_id"]
                )

                # Calculate items and totals
                calculated_items = [
                    calculate_item_totals(item.model_dump()) for item in body.items
                ]
                subtotal = sum(item["subtotal"] for item in calculated_items)
                total_tax = sum(item["tax_amount"] for item in calculated_items)

                # Apply overall discount
                if body.discount_percent > 0:
                    overall_discount = int(
                        subtotal * Decimal(str(body.discount_percent)) / 100
                    )
                else:
                    overall_discount = body.discount_amount

                # Apply overall tax if specified
                after_discount = subtotal - overall_discount
                if body.tax_rate > 0:
                    overall_tax = int(
                        after_discount * Decimal(str(body.tax_rate)) / 100
                    )
                else:
                    overall_tax = total_tax

                total_amount = after_discount + overall_tax

                # Get original invoice number if provided
                original_invoice_number = None
                if body.original_invoice_id:
                    inv = await conn.fetchrow(
                        "SELECT invoice_number FROM sales_invoices WHERE id = $1",
                        UUID(body.original_invoice_id),
                    )
                    if inv:
                        original_invoice_number = inv["invoice_number"]

                # Insert credit note
                cn_id = await conn.fetchval(
                    """
                    INSERT INTO credit_notes (
                        tenant_id, credit_note_number, customer_id, customer_name,
                        original_invoice_id, original_invoice_number,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, credit_note_date, reason, reason_detail,
                        ref_no, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                              'draft', $13, $14, $15, $16, $17, $18)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    cn_number,
                    str(body.customer_id) if body.customer_id else None,
                    body.customer_name,
                    UUID(body.original_invoice_id)
                    if body.original_invoice_id
                    else None,
                    original_invoice_number,
                    subtotal,
                    body.discount_percent,
                    overall_discount,
                    body.tax_rate,
                    overall_tax,
                    total_amount,
                    body.credit_note_date,
                    body.reason,
                    body.reason_detail,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"],
                )

                # Insert items
                for idx, item in enumerate(calculated_items, 1):
                    await conn.execute(
                        """
                        INSERT INTO credit_note_items (
                            credit_note_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        cn_id,
                        UUID(item["item_id"]) if item.get("item_id") else None,
                        item.get("item_code"),
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        item.get("discount_amount", 0),
                        item.get("tax_code"),
                        item.get("tax_rate", 0),
                        item.get("tax_amount", 0),
                        item["subtotal"],
                        item["total"],
                        idx,
                    )

                logger.info(f"Credit note created: {cn_id}, number={cn_number}")

                return {
                    "success": True,
                    "message": "Credit note created successfully",
                    "data": {
                        "id": str(cn_id),
                        "credit_note_number": cn_number,
                        "total_amount": total_amount,
                        "status": "draft",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating credit note: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create credit note")


# =============================================================================
# UPDATE CREDIT NOTE (DRAFT ONLY)
# =============================================================================


@router.patch("/{credit_note_id}", response_model=CreditNoteResponse)
async def update_credit_note(
    request: Request, credit_note_id: UUID, body: UpdateCreditNoteRequest
):
    """
    Update a draft credit note.

    Only draft credit notes can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check status
                cn = await conn.fetchrow(
                    """
                    SELECT id, status FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft credit notes can be updated"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(credit_note_id)},
                    }

                # Handle items if provided
                if "items" in update_data and update_data["items"]:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM credit_note_items WHERE credit_note_id = $1",
                        credit_note_id,
                    )

                    # Calculate and insert new items
                    calculated_items = [
                        calculate_item_totals(item.model_dump()) for item in body.items
                    ]

                    subtotal = sum(item["subtotal"] for item in calculated_items)
                    total_tax = sum(item["tax_amount"] for item in calculated_items)

                    # Recalculate totals
                    discount_percent = update_data.get("discount_percent", 0)
                    discount_amount = update_data.get("discount_amount", 0)
                    tax_rate = update_data.get("tax_rate", 0)

                    if discount_percent > 0:
                        overall_discount = int(
                            subtotal * Decimal(str(discount_percent)) / 100
                        )
                    else:
                        overall_discount = discount_amount

                    after_discount = subtotal - overall_discount

                    if tax_rate > 0:
                        overall_tax = int(after_discount * Decimal(str(tax_rate)) / 100)
                    else:
                        overall_tax = total_tax

                    total_amount = after_discount + overall_tax

                    # Update totals
                    await conn.execute(
                        """
                        UPDATE credit_notes
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """,
                        credit_note_id,
                        subtotal,
                        overall_discount,
                        overall_tax,
                        total_amount,
                    )

                    # Insert new items
                    for idx, item in enumerate(calculated_items, 1):
                        await conn.execute(
                            """
                            INSERT INTO credit_note_items (
                                credit_note_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            credit_note_id,
                            UUID(item["item_id"]) if item.get("item_id") else None,
                            item.get("item_code"),
                            item["description"],
                            item["quantity"],
                            item.get("unit"),
                            item["unit_price"],
                            item.get("discount_percent", 0),
                            item.get("discount_amount", 0),
                            item.get("tax_code"),
                            item.get("tax_rate", 0),
                            item.get("tax_amount", 0),
                            item["subtotal"],
                            item["total"],
                            idx,
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
                        if field in ("customer_id", "original_invoice_id") and value:
                            params.append(UUID(value))
                        else:
                            params.append(value)
                        param_idx += 1

                    if updates:
                        params.extend([credit_note_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE credit_notes
                            SET {', '.join(updates)}, updated_at = NOW()
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Credit note updated: {credit_note_id}")

                return {
                    "success": True,
                    "message": "Credit note updated successfully",
                    "data": {"id": str(credit_note_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update credit note")


# =============================================================================
# DELETE CREDIT NOTE (DRAFT ONLY)
# =============================================================================


@router.delete("/{credit_note_id}", response_model=CreditNoteResponse)
async def delete_credit_note(request: Request, credit_note_id: UUID):
    """
    Delete a draft credit note.

    Only draft credit notes can be deleted. Use void for posted credit notes.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check status
            cn = await conn.fetchrow(
                """
                SELECT id, status, credit_note_number FROM credit_notes
                WHERE id = $1 AND tenant_id = $2
            """,
                credit_note_id,
                ctx["tenant_id"],
            )

            if not cn:
                raise HTTPException(status_code=404, detail="Credit note not found")

            if cn["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft credit notes can be deleted. Use void for posted.",
                )

            # Delete (cascade will delete items)
            await conn.execute("DELETE FROM credit_notes WHERE id = $1", credit_note_id)

            logger.info(f"Credit note deleted: {credit_note_id}")

            return {
                "success": True,
                "message": "Credit note deleted successfully",
                "data": {
                    "id": str(credit_note_id),
                    "credit_note_number": cn["credit_note_number"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete credit note")


# =============================================================================
# POST CREDIT NOTE TO ACCOUNTING
# =============================================================================


@router.post("/{credit_note_id}/post", response_model=CreditNoteResponse)
async def post_credit_note(request: Request, credit_note_id: UUID):
    """
    Post credit note to accounting.

    Creates journal entry:
    - Dr. Sales Returns (Retur Penjualan)
    - Dr. VAT Payable (if tax)
    - Cr. Accounts Receivable

    Changes status from 'draft' to 'posted'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post credit note with status '{cn['status']}'",
                    )

                # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    cn["credit_note_date"],
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Get account IDs
                # Law 27: Resolve account IDs dynamically
                ar_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], AR_ACCOUNT_CODE
                )
                sales_return_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], SALES_RETURN_ACCOUNT_CODE
                )

                if not ar_account_id or not sales_return_account_id:
                    raise HTTPException(
                        status_code=500, detail="Required accounts not found in CoA"
                    )

                # Generate journal number
                import uuid as uuid_module

                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                journal_number = await conn.fetchval(
                    """
                    SELECT get_next_journal_number($1, 'CN')
                """,
                    ctx["tenant_id"],
                )

                if not journal_number:
                    # Fallback if function doesn't exist
                    journal_number = f"CN-{cn['credit_note_number']}"

                # Calculate amounts
                total_amount = cn["total_amount"]
                tax_amount = cn["tax_amount"] or 0
                subtotal = total_amount - tax_amount

                # Create journal entry
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    cn["credit_note_date"],
                    f"Credit Note {cn['credit_note_number']} - {cn['customer_name']}",
                    credit_note_id,
                    str(trace_id),
                    total_amount,
                    ctx["user_id"],
                )

                # Journal lines
                line_number = 1

                # Dr. Sales Returns (subtotal)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    sales_return_account_id,
                    subtotal,
                    f"Retur Penjualan - {cn['credit_note_number']}",
                )
                line_number += 1

                # Dr. VAT Payable (if tax)
                # Fase D2-wrap C4: PKP-guarded VAT_OUTPUT role resolution.
                # Tenant non-PKP tidak boleh post PPN > 0 (fail-loud 422).
                tax_jl_id = None
                if tax_amount > 0:
                    tax_account_id = await resolve_account_id_by_role_if_pkp(
                        conn, ctx["tenant_id"], "VAT_OUTPUT"
                    )

                    if tax_account_id is None:
                        raise HTTPException(
                            status_code=422,
                            detail="Tenant non-PKP tidak dapat post PPN > 0 pada credit note",
                        )

                    if tax_account_id:
                        tax_jl_id = uuid_module.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                            tax_jl_id,
                            journal_id,
                            line_number,
                            tax_account_id,
                            tax_amount,
                            f"PPN Retur - {cn['credit_note_number']}",
                        )
                        line_number += 1

                # Cr. Accounts Receivable
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    ar_account_id,
                    total_amount,
                    f"Pengurangan Piutang - {cn['credit_note_number']}",
                )

                # Law 20: Promote DRAFT -> POSTED after all lines inserted
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # ── Inventory restock for returned goods + COGS companion journal ──
                # Fase 4 fix (V164): emit companion journal Dr INVENTORY / Cr COGS @ WAC×qty
                # mirroring `record_inventory_outbound` (Sales side). Direct reversal pattern,
                # NOT wash. Atomic within same transaction as main CN journal.
                from ..services.inventory_helpers import record_inventory_inbound

                cn_items = await conn.fetch(
                    "SELECT * FROM credit_note_items WHERE credit_note_id = $1",
                    credit_note_id,
                )

                inv_restock_results = []  # [(ledger_id, total_cost)]
                companion_total_cost = Decimal("0")
                # Per-product accounts (resolved lazily once per product)
                _acct_cache = {}

                for item in cn_items:
                    if not item["item_id"]:
                        continue
                    # Check if product tracks inventory
                    product = await conn.fetchrow(
                        "SELECT id, item_code, nama_produk, track_inventory FROM products WHERE id = $1",
                        item["item_id"],
                    )
                    if not product or not product["track_inventory"]:
                        continue

                    # Get WAC for unit_cost
                    avg_cost = await conn.fetchval(
                        "SELECT get_weighted_average_cost($1, $2)",
                        ctx["tenant_id"],
                        product["id"],
                    )
                    unit_cost_val = Decimal(str(avg_cost)) if avg_cost else Decimal("0")

                    # Resolve warehouse (CN has no warehouse_id, use tenant default)
                    wh_id = await conn.fetchval(
                        "SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY created_at LIMIT 1",
                        ctx["tenant_id"],
                    )
                    if not wh_id:
                        continue

                    qty_dec = Decimal(str(item["quantity"]))
                    line_cost = qty_dec * unit_cost_val

                    inb_result = await record_inventory_inbound(
                        conn=conn,
                        tenant_id=ctx["tenant_id"],
                        product_id=product["id"],
                        product_code=product["item_code"],
                        product_name=product["nama_produk"],
                        warehouse_id=wh_id,
                        quantity=float(item["quantity"]),
                        unit_cost=float(unit_cost_val),
                        source_type="CREDIT_NOTE",
                        source_id=credit_note_id,
                        source_number=cn["credit_note_number"],
                        user_id=ctx["user_id"],
                        notes=f"Restock from Credit Note {cn['credit_note_number']}",
                        movement_date=cn["credit_note_date"],
                    )

                    if line_cost > 0:
                        # Cache per-product Inventory + COGS account resolution
                        if product["id"] not in _acct_cache:
                            prod_accts = await conn.fetchrow(
                                "SELECT cogs_account_id, inventory_account_id FROM products WHERE id = $1",
                                product["id"],
                            )
                            cogs_acct = (
                                prod_accts["cogs_account_id"] if prod_accts else None
                            )
                            inv_acct = (
                                prod_accts["inventory_account_id"]
                                if prod_accts
                                else None
                            )
                            if not cogs_acct:
                                logger.warning(
                                    "Product %s (tenant %s) has NULL cogs_account_id; "
                                    "substituting tenant COGS_SALES role default",
                                    product["id"],
                                    ctx["tenant_id"],
                                )
                                cogs_acct = await resolve_account_id_by_role(
                                    conn, ctx["tenant_id"], AccountRole.COGS_SALES
                                )
                            if not inv_acct:
                                logger.warning(
                                    "Product %s (tenant %s) has NULL inventory_account_id; "
                                    "substituting tenant INVENTORY_MERCHANDISE role default",
                                    product["id"],
                                    ctx["tenant_id"],
                                )
                                inv_acct = await resolve_account_id_by_role(
                                    conn,
                                    ctx["tenant_id"],
                                    AccountRole.INVENTORY_MERCHANDISE,
                                )
                            _acct_cache[product["id"]] = (inv_acct, cogs_acct)

                        inv_restock_results.append(
                            {
                                "ledger_id": inb_result["ledger_id"],
                                "product_id": product["id"],
                                "line_cost": line_cost,
                                "memo": f"Retur HPP - {cn['credit_note_number']} - {product['nama_produk']}",
                            }
                        )
                        companion_total_cost += line_cost

                # Emit companion journal (Dr Inventory / Cr COGS) if any tracked items
                if companion_total_cost > 0 and inv_restock_results:
                    companion_journal_id = uuid_module.uuid4()
                    companion_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'COGS-CN')",
                            ctx["tenant_id"],
                        )
                        or f"COGS-CN-{cn['credit_note_number']}"
                    )

                    # Header — DRAFT first (Law 20)
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE_COGS', $6,
                                  'DRAFT', $7, $7, $8)
                        """,
                        companion_journal_id,
                        ctx["tenant_id"],
                        companion_number,
                        cn["credit_note_date"],
                        f"COGS reversal {cn['credit_note_number']} - {cn['customer_name']}",
                        credit_note_id,
                        companion_total_cost,
                        ctx["user_id"],
                    )

                    # Lines: per-item Dr INVENTORY / aggregate Cr COGS
                    ln = 1
                    # Aggregate COGS lines per product (single Cr per product is also fine,
                    # but a single Cr COGS for the total is cleanest).
                    # Inventory side: one Dr line per ledger entry for traceability.
                    for r in inv_restock_results:
                        inv_acct_id, _cogs_acct_id = _acct_cache[r["product_id"]]
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                            """,
                            uuid_module.uuid4(),
                            companion_journal_id,
                            ln,
                            inv_acct_id,
                            r["line_cost"],
                            r["memo"],
                        )
                        ln += 1

                    # Single aggregate Cr COGS line (per-tenant COGS account; cache shows
                    # all products mapped to same COGS_SALES role in normal tenants).
                    # If products have heterogeneous COGS accounts, emit one Cr per account.
                    cogs_buckets: dict = {}
                    for r in inv_restock_results:
                        _inv, cogs_acct_id = _acct_cache[r["product_id"]]
                        cogs_buckets[cogs_acct_id] = (
                            cogs_buckets.get(cogs_acct_id, Decimal("0"))
                            + r["line_cost"]
                        )

                    for cogs_acct_id, total in cogs_buckets.items():
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                            """,
                            uuid_module.uuid4(),
                            companion_journal_id,
                            ln,
                            cogs_acct_id,
                            total,
                            f"Retur HPP - {cn['credit_note_number']}",
                        )
                        ln += 1

                    # Law 20: Promote DRAFT -> POSTED
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        companion_journal_id,
                    )

                    # Link inventory_ledger rows just created to the companion journal
                    for r in inv_restock_results:
                        await conn.execute(
                            "UPDATE inventory_ledger SET journal_id = $1 WHERE id = $2",
                            companion_journal_id,
                            r["ledger_id"],
                        )

                # Wave 3: Write document_tax_lines (PPN reversal on CN)
                if tax_amount > 0 and tax_jl_id:
                    # Resolve PPN tax_code from tax_codes
                    ppn_tc_id = await conn.fetchval(
                        """
                        SELECT id FROM tax_codes
                        WHERE tenant_id = $1 AND tax_type = 'ppn' AND rate > 0 AND is_active = true
                        LIMIT 1
                        """,
                        ctx["tenant_id"],
                    )
                    if ppn_tc_id:
                        tc_coa = await conn.fetchval(
                            "SELECT coa_id FROM tax_codes WHERE id = $1",
                            ppn_tc_id,
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
                            uuid_module.uuid4(),
                            ctx["tenant_id"],
                            "CREDIT_NOTE",
                            credit_note_id,
                            ppn_tc_id,
                            "output",
                            dpp_val,
                            float(tax_amount),
                            tc_coa,
                            tax_jl_id,
                        )

                # Update credit note status
                await conn.execute(
                    """
                    UPDATE credit_notes
                    SET status = 'posted', journal_id = $2,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    credit_note_id,
                    journal_id,
                    ctx["user_id"],
                )

                logger.info(
                    f"Credit note posted: {credit_note_id}, journal={journal_id}"
                )

                return {
                    "success": True,
                    "message": "Credit note posted to accounting",
                    "data": {
                        "id": str(credit_note_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post credit note")


# =============================================================================
# APPLY CREDIT NOTE TO INVOICE(S)
# =============================================================================


@router.post("/{credit_note_id}/apply", response_model=CreditNoteResponse)
async def apply_credit_note(
    request: Request, credit_note_id: UUID, body: ApplyCreditNoteRequest
):
    """
    Apply credit note to one or more invoices.

    Reduces the invoice's outstanding balance.
    Credit note must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_APPLY:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot apply credit note with status '{cn['status']}'",
                    )

                # Calculate remaining
                remaining = (
                    cn["total_amount"]
                    - (cn["amount_applied"] or 0)
                    - (cn["amount_refunded"] or 0)
                )
                total_to_apply = sum(app.amount for app in body.applications)

                if total_to_apply > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Application amount ({total_to_apply}) exceeds remaining balance ({remaining})",
                    )

                application_date = body.application_date or date.today()
                applications_created = []

                for app in body.applications:
                    # Validate invoice
                    invoice = await conn.fetchrow(
                        """
                        SELECT id, customer_id, total_amount, status
                        FROM sales_invoices
                        WHERE id = $1 AND tenant_id = $2
                    """,
                        UUID(app.invoice_id),
                        ctx["tenant_id"],
                    )

                    if not invoice:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {app.invoice_id} not found",
                        )

                    # Verify same customer
                    if (
                        cn["customer_id"]
                        and invoice["customer_id"] != cn["customer_id"]
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {app.invoice_id} belongs to different customer",
                        )

                    # Check invoice has balance (Law 16: journal-based)
                    invoice_remaining = await get_invoice_remaining_from_journal(
                        conn, ctx["tenant_id"], UUID(app.invoice_id)
                    )
                    if app.amount > invoice_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail="Application amount exceeds invoice remaining balance",
                        )

                    # Check for existing application
                    existing = await conn.fetchval(
                        """
                        SELECT id FROM credit_note_applications
                        WHERE credit_note_id = $1 AND invoice_id = $2
                    """,
                        credit_note_id,
                        UUID(app.invoice_id),
                    )

                    if existing:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Credit note already applied to invoice {app.invoice_id}",
                        )

                    # Create application
                    import uuid as uuid_module

                    app_id = uuid_module.uuid4()

                    await conn.execute(
                        """
                        INSERT INTO credit_note_applications (
                            id, tenant_id, credit_note_id, invoice_id,
                            amount_applied, application_date, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        app_id,
                        ctx["tenant_id"],
                        credit_note_id,
                        UUID(app.invoice_id),
                        app.amount,
                        application_date,
                        ctx["user_id"],
                    )

                    # Update invoice (derive amount_paid from journal-based remaining)
                    new_amount_paid = (
                        invoice["total_amount"] - int(invoice_remaining) + app.amount
                    )
                    new_status = (
                        "paid"
                        if new_amount_paid >= invoice["total_amount"]
                        else "partial"
                    )

                    await conn.execute(
                        """
                        UPDATE sales_invoices
                        SET amount_paid = $2, status = $3, updated_at = NOW()
                        WHERE id = $1
                    """,
                        UUID(app.invoice_id),
                        new_amount_paid,
                        new_status,
                    )

                    # Update AR if exists
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET amount_paid = amount_paid + $2,
                            status = CASE
                                WHEN amount_paid + $2 >= amount THEN 'PAID'
                                ELSE 'PARTIAL'
                            END,
                            updated_at = NOW()
                        WHERE source_id = $1 AND source_type = 'INVOICE'
                    """,
                        UUID(app.invoice_id),
                        app.amount,
                    )

                    applications_created.append(
                        {
                            "application_id": str(app_id),
                            "invoice_id": app.invoice_id,
                            "amount": app.amount,
                        }
                    )

                # Credit note status will be updated by trigger
                logger.info(
                    f"Credit note applied: {credit_note_id}, applications={len(applications_created)}"
                )

                return {
                    "success": True,
                    "message": f"Credit note applied to {len(applications_created)} invoice(s)",
                    "data": {
                        "id": str(credit_note_id),
                        "applications": applications_created,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply credit note")


# =============================================================================
# REFUND CREDIT NOTE
# =============================================================================


@router.post("/{credit_note_id}/refund", response_model=CreditNoteResponse)
async def refund_credit_note(
    request: Request, credit_note_id: UUID, body: RefundCreditNoteRequest
):
    """
    Issue a cash refund from credit note.

    Creates journal entry:
    - Dr. Accounts Receivable
    - Cr. Cash/Bank

    Credit note must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_REFUND:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot refund credit note with status '{cn['status']}'",
                    )

                # Check remaining
                remaining = (
                    cn["total_amount"]
                    - (cn["amount_applied"] or 0)
                    - (cn["amount_refunded"] or 0)
                )

                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount ({body.amount}) exceeds remaining balance ({remaining})",
                    )

                # Validate account — resolve from bank_account_id if provided
                if body.bank_account_id:
                    bank_acc = await conn.fetchrow(
                        "SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                        UUID(body.bank_account_id),
                        ctx["tenant_id"],
                    )
                    if not bank_acc:
                        raise HTTPException(
                            status_code=400, detail="Bank account not found"
                        )
                    resolved_account_id = bank_acc["coa_id"]
                elif body.account_id:
                    resolved_account_id = UUID(body.account_id)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Either account_id or bank_account_id is required",
                    )

                account = await conn.fetchrow(
                    """
                    SELECT id, account_code as code, name FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    resolved_account_id,
                    ctx["tenant_id"],
                )

                if not account:
                    raise HTTPException(
                        status_code=400, detail="Payment account not found"
                    )

                import uuid as uuid_module

                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    body.refund_date,
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Create refund journal
                journal_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RF')", ctx["tenant_id"]
                    )
                    or f"RF-{cn['credit_note_number']}"
                )

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE_REFUND', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund {cn['credit_note_number']} - {cn['customer_name']}",
                    credit_note_id,
                    str(trace_id),
                    body.amount,
                    ctx["user_id"],
                )

                # Get AR account
                # Law 27: Resolve AR account dynamically
                ar_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], AR_ACCOUNT_CODE
                )

                # Dr. AR (reverse the credit)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    ar_account_id,
                    body.amount,
                    f"Refund Piutang - {cn['credit_note_number']}",
                )

                # Cr. Cash/Bank
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    UUID(body.account_id),
                    body.amount,
                    f"Pembayaran Refund - {cn['credit_note_number']}",
                )

                # Law 20: Promote DRAFT -> POSTED after all lines inserted
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Create refund record
                await conn.execute(
                    """
                    INSERT INTO credit_note_refunds (
                        id, tenant_id, credit_note_id, amount, refund_date,
                        payment_method, account_id, bank_account_id,
                        reference, notes, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    refund_id,
                    ctx["tenant_id"],
                    credit_note_id,
                    body.amount,
                    body.refund_date,
                    body.payment_method,
                    UUID(body.account_id),
                    UUID(body.bank_account_id) if body.bank_account_id else None,
                    body.reference,
                    body.notes,
                    journal_id,
                    ctx["user_id"],
                )

                # Status will be updated by trigger
                logger.info(
                    f"Credit note refunded: {credit_note_id}, amount={body.amount}"
                )

                return {
                    "success": True,
                    "message": "Refund issued successfully",
                    "data": {
                        "id": str(credit_note_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error refunding credit note {credit_note_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to issue refund")


# =============================================================================
# VOID CREDIT NOTE
# =============================================================================


@router.post("/{credit_note_id}/void", response_model=CreditNoteResponse)
async def void_credit_note(
    request: Request, credit_note_id: UUID, body: VoidCreditNoteRequest
):
    """
    Void a credit note.

    Creates reversal journal entry.
    Credit note must have no applications or refunds.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_VOID:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] == "void":
                    raise HTTPException(
                        status_code=400, detail="Credit note already voided"
                    )

                if cn["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM credit_notes WHERE id = $1", credit_note_id
                    )
                    return {
                        "success": True,
                        "message": "Draft credit note deleted",
                        "data": {"id": str(credit_note_id)},
                    }

                # Check for applications or refunds
                if (cn["amount_applied"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void credit note with applications. Reverse applications first.",
                    )

                if (cn["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void credit note with refunds. Reverse refunds first.",
                    )

                    # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    date.today(),
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Create reversal journal if original was posted
                if cn["journal_id"]:
                    import uuid as uuid_module

                    reversal_journal_id = uuid_module.uuid4()

                    # Get original journal lines
                    original_lines = await conn.fetch(
                        """
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """,
                        cn["journal_id"],
                    )

                    journal_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'RV')", ctx["tenant_id"]
                        )
                        or f"RV-{cn['credit_note_number']}"
                    )

                    # Create reversal header
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'CREDIT_NOTE', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {cn['credit_note_number']} - {cn['customer_name']}",
                        credit_note_id,
                        cn["journal_id"],
                        cn["total_amount"],
                        ctx["user_id"],
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap: original credit becomes debit
                            line["debit"],  # Swap: original debit becomes credit
                            f"Reversal - {line['memo'] or ''}",
                        )

                        # Law 20: Promote DRAFT -> POSTED after all lines inserted
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # Mark original journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                    """,
                        cn["journal_id"],
                        reversal_journal_id,
                    )

                # ── Fase 4 (V164): Reverse COGS companion journal + inventory_ledger ──
                companion = await conn.fetchrow(
                    """
                    SELECT id, total_debit FROM journal_entries
                    WHERE tenant_id = $1
                      AND source_type = 'CREDIT_NOTE_COGS'
                      AND source_id = $2
                      AND status = 'POSTED'
                      AND reversed_by_id IS NULL
                    LIMIT 1
                    """,
                    ctx["tenant_id"],
                    credit_note_id,
                )
                if companion:
                    import uuid as uuid_module2

                    companion_rev_id = uuid_module2.uuid4()
                    companion_rev_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'RV-COGS-CN')",
                            ctx["tenant_id"],
                        )
                        or f"RV-COGS-CN-{cn['credit_note_number']}"
                    )
                    companion_orig_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1",
                        companion["id"],
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4,
                                  'CREDIT_NOTE_COGS', $5, $6, 'DRAFT', $7, $7, $8)
                        """,
                        companion_rev_id,
                        ctx["tenant_id"],
                        companion_rev_number,
                        f"Void COGS companion {cn['credit_note_number']}",
                        credit_note_id,
                        companion["id"],
                        companion["total_debit"],
                        ctx["user_id"],
                    )
                    for idx, line in enumerate(companion_orig_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                            """,
                            uuid_module2.uuid4(),
                            companion_rev_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # swap
                            line["debit"],
                            f"Reversal - {line['memo'] or ''}",
                        )
                    # Law 20: DRAFT -> POSTED
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        companion_rev_id,
                    )
                    # Mark original companion VOID (Law 14 mirror — single reversal)
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                        """,
                        companion["id"],
                        companion_rev_id,
                    )

                    # Reverse inventory_ledger entries (CREDIT_NOTE source) -> CREDIT_NOTE_VOID
                    from ..services.inventory_helpers import record_inventory_reversal

                    await record_inventory_reversal(
                        conn=conn,
                        tenant_id=ctx["tenant_id"],
                        source_type="CREDIT_NOTE",
                        source_id=credit_note_id,
                        reversal_journal_id=companion_rev_id,
                        created_by=ctx["user_id"],
                        notes_prefix="VOID_CN",
                    )

                # Update credit note status
                await conn.execute(
                    """
                    UPDATE credit_notes
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    credit_note_id,
                    ctx["user_id"],
                    body.reason,
                )

                logger.info(f"Credit note voided: {credit_note_id}")

                return {
                    "success": True,
                    "message": "Credit note voided successfully",
                    "data": {"id": str(credit_note_id), "status": "void"},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void credit note")


@router.post("/{credit_note_id}/create-tax-invoice")
async def create_tax_invoice_from_cn(request: Request, credit_note_id: str):
    """Create faktur pajak retur keluaran dari credit note."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        tid = ctx["tenant_id"]
        await conn.execute(f"SET LOCAL app.tenant_id = '{tid}'")
        cn = await conn.fetchrow(
            "SELECT id, status, tax_invoice_id FROM credit_notes "
            "WHERE id = $1 AND tenant_id = $2",
            credit_note_id,
            ctx["tenant_id"],
        )
        if not cn:
            raise HTTPException(404, "Credit note tidak ditemukan")
        if cn["status"] == "draft":
            raise HTTPException(400, "Credit note belum di-post")
        if cn["tax_invoice_id"]:
            raise HTTPException(400, "Credit note sudah punya faktur pajak")

    # Delegate to main tax-invoices create endpoint via internal HTTP
    import httpx

    auth_header = request.headers.get("authorization", "")
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            "http://localhost:8000/api/tax-invoices",
            json={"source_type": "credit_note", "source_ids": [credit_note_id]},
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            timeout=30.0,
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    return resp.json()
