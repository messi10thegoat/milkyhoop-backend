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
    CreditNoteItemResponse,
    CreditNoteApplicationResponse,
    CreditNoteRefundResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
AR_ACCOUNT = "1-10300"         # Piutang Usaha
SALES_RETURN_ACCOUNT = "4-10300"  # Retur Penjualan
TAX_PAYABLE_ACCOUNT = "2-10300"   # PPN Keluaran


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
    """Calculate item totals with discount and tax."""
    quantity = Decimal(str(item.get('quantity', 0)))
    unit_price = Decimal(str(item.get('unit_price', 0)))
    discount_percent = Decimal(str(item.get('discount_percent', 0)))
    discount_amount = Decimal(str(item.get('discount_amount', 0)))
    tax_rate = Decimal(str(item.get('tax_rate', 0)))

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
        'subtotal': int(subtotal),
        'discount_amount': int(discount),
        'tax_amount': int(tax_amount),
        'total': int(total)
    }


# =============================================================================
# LIST CREDIT NOTES
# =============================================================================

@router.get("", response_model=CreditNoteListResponse)
async def list_credit_notes(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "partial", "applied", "void"]] = Query("all"),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by number or customer name"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["credit_note_date", "credit_note_number", "total_amount", "created_at"] = Query("created_at"),
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
                params.append(UUID(customer_id))
                param_idx += 1

            if search:
                conditions.append(
                    f"(credit_note_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
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
                "created_at": "created_at"
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
                    "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
                    "customer_name": row["customer_name"],
                    "credit_note_date": row["credit_note_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["total_amount"] - (row["amount_applied"] or 0) - (row["amount_refunded"] or 0),
                    "status": row["status"],
                    "reason": row["reason"],
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
            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE status = 'applied') as applied_count,
                    COALESCE(SUM(total_amount), 0) as total_value,
                    COALESCE(SUM(amount_applied), 0) as total_applied,
                    COALESCE(SUM(amount_refunded), 0) as total_refunded,
                    COALESCE(SUM(total_amount - COALESCE(amount_applied, 0) - COALESCE(amount_refunded, 0))
                        FILTER (WHERE status IN ('posted', 'partial')), 0) as available_balance
                FROM credit_notes
                WHERE tenant_id = $1 AND status != 'void'
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
                }
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
            cn = await conn.fetchrow("""
                SELECT * FROM credit_notes
                WHERE id = $1 AND tenant_id = $2
            """, credit_note_id, ctx["tenant_id"])

            if not cn:
                raise HTTPException(status_code=404, detail="Credit note not found")

            # Get items
            items = await conn.fetch("""
                SELECT * FROM credit_note_items
                WHERE credit_note_id = $1
                ORDER BY line_number
            """, credit_note_id)

            # Get applications with invoice numbers
            applications = await conn.fetch("""
                SELECT a.*, i.invoice_number
                FROM credit_note_applications a
                LEFT JOIN sales_invoices i ON a.invoice_id = i.id
                WHERE a.credit_note_id = $1
                ORDER BY a.application_date
            """, credit_note_id)

            # Get refunds
            refunds = await conn.fetch("""
                SELECT * FROM credit_note_refunds
                WHERE credit_note_id = $1
                ORDER BY refund_date
            """, credit_note_id)

            # Build response
            remaining = cn["total_amount"] - (cn["amount_applied"] or 0) - (cn["amount_refunded"] or 0)

            return {
                "success": True,
                "data": {
                    "id": str(cn["id"]),
                    "credit_note_number": cn["credit_note_number"],
                    "customer_id": str(cn["customer_id"]) if cn["customer_id"] else None,
                    "customer_name": cn["customer_name"],
                    "original_invoice_id": str(cn["original_invoice_id"]) if cn["original_invoice_id"] else None,
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
                            "item_id": str(item["item_id"]) if item["item_id"] else None,
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
                    "posted_at": cn["posted_at"].isoformat() if cn["posted_at"] else None,
                    "posted_by": str(cn["posted_by"]) if cn["posted_by"] else None,
                    "voided_at": cn["voided_at"].isoformat() if cn["voided_at"] else None,
                    "voided_reason": cn["voided_reason"],
                    "created_at": cn["created_at"].isoformat(),
                    "updated_at": cn["updated_at"].isoformat(),
                    "created_by": str(cn["created_by"]) if cn["created_by"] else None,
                }
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
                    "SELECT generate_credit_note_number($1, 'CN')",
                    ctx["tenant_id"]
                )

                # Calculate items and totals
                calculated_items = [calculate_item_totals(item.model_dump()) for item in body.items]
                subtotal = sum(item['subtotal'] for item in calculated_items)
                total_tax = sum(item['tax_amount'] for item in calculated_items)

                # Apply overall discount
                if body.discount_percent > 0:
                    overall_discount = int(subtotal * Decimal(str(body.discount_percent)) / 100)
                else:
                    overall_discount = body.discount_amount

                # Apply overall tax if specified
                after_discount = subtotal - overall_discount
                if body.tax_rate > 0:
                    overall_tax = int(after_discount * Decimal(str(body.tax_rate)) / 100)
                else:
                    overall_tax = total_tax

                total_amount = after_discount + overall_tax

                # Get original invoice number if provided
                original_invoice_number = None
                if body.original_invoice_id:
                    inv = await conn.fetchrow(
                        "SELECT invoice_number FROM sales_invoices WHERE id = $1",
                        UUID(body.original_invoice_id)
                    )
                    if inv:
                        original_invoice_number = inv["invoice_number"]

                # Insert credit note
                cn_id = await conn.fetchval("""
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
                    UUID(body.customer_id) if body.customer_id else None,
                    body.customer_name,
                    UUID(body.original_invoice_id) if body.original_invoice_id else None,
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
                    ctx["user_id"]
                )

                # Insert items
                for idx, item in enumerate(calculated_items, 1):
                    await conn.execute("""
                        INSERT INTO credit_note_items (
                            credit_note_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        cn_id,
                        UUID(item['item_id']) if item.get('item_id') else None,
                        item.get('item_code'),
                        item['description'],
                        item['quantity'],
                        item.get('unit'),
                        item['unit_price'],
                        item.get('discount_percent', 0),
                        item.get('discount_amount', 0),
                        item.get('tax_code'),
                        item.get('tax_rate', 0),
                        item.get('tax_amount', 0),
                        item['subtotal'],
                        item['total'],
                        idx
                    )

                logger.info(f"Credit note created: {cn_id}, number={cn_number}")

                return {
                    "success": True,
                    "message": "Credit note created successfully",
                    "data": {
                        "id": str(cn_id),
                        "credit_note_number": cn_number,
                        "total_amount": total_amount,
                        "status": "draft"
                    }
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
async def update_credit_note(request: Request, credit_note_id: UUID, body: UpdateCreditNoteRequest):
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
                cn = await conn.fetchrow("""
                    SELECT id, status FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """, credit_note_id, ctx["tenant_id"])

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft credit notes can be updated"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(credit_note_id)}
                    }

                # Handle items if provided
                if "items" in update_data and update_data["items"]:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM credit_note_items WHERE credit_note_id = $1",
                        credit_note_id
                    )

                    # Calculate and insert new items
                    calculated_items = [
                        calculate_item_totals(item.model_dump())
                        for item in body.items
                    ]

                    subtotal = sum(item['subtotal'] for item in calculated_items)
                    total_tax = sum(item['tax_amount'] for item in calculated_items)

                    # Recalculate totals
                    discount_percent = update_data.get('discount_percent', 0)
                    discount_amount = update_data.get('discount_amount', 0)
                    tax_rate = update_data.get('tax_rate', 0)

                    if discount_percent > 0:
                        overall_discount = int(subtotal * Decimal(str(discount_percent)) / 100)
                    else:
                        overall_discount = discount_amount

                    after_discount = subtotal - overall_discount

                    if tax_rate > 0:
                        overall_tax = int(after_discount * Decimal(str(tax_rate)) / 100)
                    else:
                        overall_tax = total_tax

                    total_amount = after_discount + overall_tax

                    # Update totals
                    await conn.execute("""
                        UPDATE credit_notes
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """, credit_note_id, subtotal, overall_discount, overall_tax, total_amount)

                    # Insert new items
                    for idx, item in enumerate(calculated_items, 1):
                        await conn.execute("""
                            INSERT INTO credit_note_items (
                                credit_note_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            credit_note_id,
                            UUID(item['item_id']) if item.get('item_id') else None,
                            item.get('item_code'),
                            item['description'],
                            item['quantity'],
                            item.get('unit'),
                            item['unit_price'],
                            item.get('discount_percent', 0),
                            item.get('discount_amount', 0),
                            item.get('tax_code'),
                            item.get('tax_rate', 0),
                            item.get('tax_amount', 0),
                            item['subtotal'],
                            item['total'],
                            idx
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
                    "data": {"id": str(credit_note_id)}
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
            cn = await conn.fetchrow("""
                SELECT id, status, credit_note_number FROM credit_notes
                WHERE id = $1 AND tenant_id = $2
            """, credit_note_id, ctx["tenant_id"])

            if not cn:
                raise HTTPException(status_code=404, detail="Credit note not found")

            if cn["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft credit notes can be deleted. Use void for posted."
                )

            # Delete (cascade will delete items)
            await conn.execute(
                "DELETE FROM credit_notes WHERE id = $1",
                credit_note_id
            )

            logger.info(f"Credit note deleted: {credit_note_id}")

            return {
                "success": True,
                "message": "Credit note deleted successfully",
                "data": {
                    "id": str(credit_note_id),
                    "credit_note_number": cn["credit_note_number"]
                }
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

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get credit note
                cn = await conn.fetchrow("""
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """, credit_note_id, ctx["tenant_id"])

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post credit note with status '{cn['status']}'"
                    )

                # Get account IDs
                ar_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], AR_ACCOUNT)

                sales_return_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], SALES_RETURN_ACCOUNT)

                if not ar_account_id or not sales_return_account_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Required accounts not found in CoA"
                    )

                # Generate journal number
                import uuid as uuid_module
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                journal_number = await conn.fetchval("""
                    SELECT get_next_journal_number($1, 'CN')
                """, ctx["tenant_id"])

                if not journal_number:
                    # Fallback if function doesn't exist
                    journal_number = f"CN-{cn['credit_note_number']}"

                # Calculate amounts
                total_amount = cn["total_amount"]
                tax_amount = cn["tax_amount"] or 0
                subtotal = total_amount - tax_amount

                # Create journal entry
                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    cn["credit_note_date"],
                    f"Credit Note {cn['credit_note_number']} - {cn['customer_name']}",
                    credit_note_id,
                    str(trace_id),
                    float(total_amount),
                    ctx["user_id"]
                )

                # Journal lines
                line_number = 1

                # Dr. Sales Returns (subtotal)
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    sales_return_account_id,
                    float(subtotal),
                    f"Retur Penjualan - {cn['credit_note_number']}"
                )
                line_number += 1

                # Dr. VAT Payable (if tax)
                if tax_amount > 0:
                    tax_account_id = await conn.fetchval("""
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1 AND account_code = $2
                    """, ctx["tenant_id"], TAX_PAYABLE_ACCOUNT)

                    if tax_account_id:
                        await conn.execute("""
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                            uuid_module.uuid4(),
                            journal_id,
                            line_number,
                            tax_account_id,
                            float(tax_amount),
                            f"PPN Retur - {cn['credit_note_number']}"
                        )
                        line_number += 1

                # Cr. Accounts Receivable
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    ar_account_id,
                    float(total_amount),
                    f"Pengurangan Piutang - {cn['credit_note_number']}"
                )

                # Update credit note status
                await conn.execute("""
                    UPDATE credit_notes
                    SET status = 'posted', journal_id = $2,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1
                """, credit_note_id, journal_id, ctx["user_id"])

                logger.info(f"Credit note posted: {credit_note_id}, journal={journal_id}")

                return {
                    "success": True,
                    "message": "Credit note posted to accounting",
                    "data": {
                        "id": str(credit_note_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted"
                    }
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
async def apply_credit_note(request: Request, credit_note_id: UUID, body: ApplyCreditNoteRequest):
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
                # Get credit note
                cn = await conn.fetchrow("""
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """, credit_note_id, ctx["tenant_id"])

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot apply credit note with status '{cn['status']}'"
                    )

                # Calculate remaining
                remaining = cn["total_amount"] - (cn["amount_applied"] or 0) - (cn["amount_refunded"] or 0)
                total_to_apply = sum(app.amount for app in body.applications)

                if total_to_apply > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Application amount ({total_to_apply}) exceeds remaining balance ({remaining})"
                    )

                application_date = body.application_date or date.today()
                applications_created = []

                for app in body.applications:
                    # Validate invoice
                    invoice = await conn.fetchrow("""
                        SELECT id, customer_id, total_amount, amount_paid, status
                        FROM sales_invoices
                        WHERE id = $1 AND tenant_id = $2
                    """, UUID(app.invoice_id), ctx["tenant_id"])

                    if not invoice:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {app.invoice_id} not found"
                        )

                    # Verify same customer
                    if cn["customer_id"] and invoice["customer_id"] != cn["customer_id"]:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {app.invoice_id} belongs to different customer"
                        )

                    # Check invoice has balance
                    invoice_remaining = invoice["total_amount"] - (invoice["amount_paid"] or 0)
                    if app.amount > invoice_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Application amount exceeds invoice remaining balance"
                        )

                    # Check for existing application
                    existing = await conn.fetchval("""
                        SELECT id FROM credit_note_applications
                        WHERE credit_note_id = $1 AND invoice_id = $2
                    """, credit_note_id, UUID(app.invoice_id))

                    if existing:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Credit note already applied to invoice {app.invoice_id}"
                        )

                    # Create application
                    import uuid as uuid_module
                    app_id = uuid_module.uuid4()

                    await conn.execute("""
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
                        ctx["user_id"]
                    )

                    # Update invoice
                    new_amount_paid = (invoice["amount_paid"] or 0) + app.amount
                    new_status = "paid" if new_amount_paid >= invoice["total_amount"] else "partial"

                    await conn.execute("""
                        UPDATE sales_invoices
                        SET amount_paid = $2, status = $3, updated_at = NOW()
                        WHERE id = $1
                    """, UUID(app.invoice_id), new_amount_paid, new_status)

                    # Update AR if exists
                    await conn.execute("""
                        UPDATE accounts_receivable
                        SET amount_paid = amount_paid + $2,
                            status = CASE
                                WHEN amount_paid + $2 >= amount THEN 'PAID'
                                ELSE 'PARTIAL'
                            END,
                            updated_at = NOW()
                        WHERE source_id = $1 AND source_type = 'INVOICE'
                    """, UUID(app.invoice_id), app.amount)

                    applications_created.append({
                        "application_id": str(app_id),
                        "invoice_id": app.invoice_id,
                        "amount": app.amount
                    })

                # Credit note status will be updated by trigger
                logger.info(f"Credit note applied: {credit_note_id}, applications={len(applications_created)}")

                return {
                    "success": True,
                    "message": f"Credit note applied to {len(applications_created)} invoice(s)",
                    "data": {
                        "id": str(credit_note_id),
                        "applications": applications_created
                    }
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
async def refund_credit_note(request: Request, credit_note_id: UUID, body: RefundCreditNoteRequest):
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
                # Get credit note
                cn = await conn.fetchrow("""
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """, credit_note_id, ctx["tenant_id"])

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot refund credit note with status '{cn['status']}'"
                    )

                # Check remaining
                remaining = cn["total_amount"] - (cn["amount_applied"] or 0) - (cn["amount_refunded"] or 0)

                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount ({body.amount}) exceeds remaining balance ({remaining})"
                    )

                # Validate account
                account = await conn.fetchrow("""
                    SELECT id, account_code as code, name FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.account_id), ctx["tenant_id"])

                if not account:
                    raise HTTPException(status_code=400, detail="Payment account not found")

                import uuid as uuid_module
                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                # Create refund journal
                journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, 'RF')",
                    ctx["tenant_id"]
                ) or f"RF-{cn['credit_note_number']}"

                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE_REFUND', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund {cn['credit_note_number']} - {cn['customer_name']}",
                    credit_note_id,
                    str(trace_id),
                    float(body.amount),
                    ctx["user_id"]
                )

                # Get AR account
                ar_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], AR_ACCOUNT)

                # Dr. AR (reverse the credit)
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    ar_account_id,
                    float(body.amount),
                    f"Refund Piutang - {cn['credit_note_number']}"
                )

                # Cr. Cash/Bank
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    UUID(body.account_id),
                    float(body.amount),
                    f"Pembayaran Refund - {cn['credit_note_number']}"
                )

                # Create refund record
                await conn.execute("""
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
                    ctx["user_id"]
                )

                # Status will be updated by trigger
                logger.info(f"Credit note refunded: {credit_note_id}, amount={body.amount}")

                return {
                    "success": True,
                    "message": "Refund issued successfully",
                    "data": {
                        "id": str(credit_note_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refunding credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to issue refund")


# =============================================================================
# VOID CREDIT NOTE
# =============================================================================

@router.post("/{credit_note_id}/void", response_model=CreditNoteResponse)
async def void_credit_note(request: Request, credit_note_id: UUID, body: VoidCreditNoteRequest):
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
                # Get credit note
                cn = await conn.fetchrow("""
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """, credit_note_id, ctx["tenant_id"])

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] == "void":
                    raise HTTPException(status_code=400, detail="Credit note already voided")

                if cn["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM credit_notes WHERE id = $1",
                        credit_note_id
                    )
                    return {
                        "success": True,
                        "message": "Draft credit note deleted",
                        "data": {"id": str(credit_note_id)}
                    }

                # Check for applications or refunds
                if (cn["amount_applied"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void credit note with applications. Reverse applications first."
                    )

                if (cn["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void credit note with refunds. Reverse refunds first."
                    )

                # Create reversal journal if original was posted
                if cn["journal_id"]:
                    import uuid as uuid_module
                    reversal_journal_id = uuid_module.uuid4()

                    # Get original journal lines
                    original_lines = await conn.fetch("""
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """, cn["journal_id"])

                    journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RV')",
                        ctx["tenant_id"]
                    ) or f"RV-{cn['credit_note_number']}"

                    # Create reversal header
                    await conn.execute("""
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'CREDIT_NOTE', $5, $6, 'POSTED', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {cn['credit_note_number']} - {cn['customer_name']}",
                        credit_note_id,
                        cn["journal_id"],
                        float(cn["total_amount"]),
                        ctx["user_id"]
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute("""
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap: original credit becomes debit
                            line["debit"],   # Swap: original debit becomes credit
                            f"Reversal - {line['memo'] or ''}"
                        )

                    # Mark original journal as reversed
                    await conn.execute("""
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                    """, cn["journal_id"], reversal_journal_id)

                # Update credit note status
                await conn.execute("""
                    UPDATE credit_notes
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """, credit_note_id, ctx["user_id"], body.reason)

                logger.info(f"Credit note voided: {credit_note_id}")

                return {
                    "success": True,
                    "message": "Credit note voided successfully",
                    "data": {
                        "id": str(credit_note_id),
                        "status": "void"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void credit note")
