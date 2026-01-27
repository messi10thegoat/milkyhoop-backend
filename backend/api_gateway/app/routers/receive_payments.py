"""
Receive Payments Router - Penerimaan Pembayaran

Endpoints for managing customer payments for invoices.
Integrates with customer_deposits for deposit payments and overpayments.

Flow:
1. Create receive payment (draft or posted)
2. Allocate to invoice(s)
3. Post to accounting (creates journal entry)
4. Void if needed (creates reversing journal)

Journal Entry on POST (Cash/Bank):
    Dr. Kas/Bank                        total_amount
    Dr. Potongan Penjualan (if any)     discount_amount
        Cr. Piutang Usaha                   allocated_amount
        Cr. Uang Muka Pelanggan (if any)    unapplied_amount

Journal Entry on POST (From Deposit):
    Dr. Uang Muka Pelanggan             total_amount
        Cr. Piutang Usaha                   allocated_amount

Endpoints:
- GET    /receive-payments              - List receive payments
- GET    /receive-payments/summary      - Summary statistics
- GET    /receive-payments/{id}         - Get payment detail
- POST   /receive-payments              - Create payment
- PUT    /receive-payments/{id}         - Update draft payment
- DELETE /receive-payments/{id}         - Delete draft payment
- POST   /receive-payments/{id}/post    - Post to accounting
- POST   /receive-payments/{id}/void    - Void payment
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date

from ..schemas.receive_payments import (
    CreateReceivePaymentRequest,
    ReceivePaymentDetailResponse,
    ReceivePaymentListResponse,
    ReceivePaymentResponse,
    ReceivePaymentSummaryResponse,
    UpdateReceivePaymentRequest,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
CUSTOMER_DEPOSIT_ACCOUNT = "2-10400"  # Uang Muka Pelanggan (Liability)
AR_ACCOUNT = "1-10300"  # Piutang Usaha


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

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


# =============================================================================
# LIST RECEIVE PAYMENTS
# =============================================================================


@router.get("", response_model=ReceivePaymentListResponse)
async def list_receive_payments(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "voided"]] = Query("all"),
    customer_id: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    source_type: Optional[Literal["all", "cash", "deposit"]] = Query("all"),
    search: Optional[str] = Query(
        None, description="Search by payment number or customer name"
    ),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal[
        "payment_date", "payment_number", "customer_name", "total_amount", "created_at"
    ] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List receive payments with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

            # Build query conditions
            conditions = ["rp.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"rp.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"rp.customer_id = ${param_idx}")
                params.append(UUID(customer_id))
                param_idx += 1

            if payment_method:
                conditions.append(f"rp.payment_method = ${param_idx}")
                params.append(payment_method)
                param_idx += 1

            if source_type and source_type != "all":
                conditions.append(f"rp.source_type = ${param_idx}")
                params.append(source_type)
                param_idx += 1

            if search:
                conditions.append(
                    f"(rp.payment_number ILIKE ${param_idx} OR rp.customer_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"rp.payment_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"rp.payment_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort mapping
            sort_mapping = {
                "payment_date": "rp.payment_date",
                "payment_number": "rp.payment_number",
                "customer_name": "rp.customer_name",
                "total_amount": "rp.total_amount",
                "created_at": "rp.created_at",
            }
            sort_field = sort_mapping.get(sort_by, "rp.created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"""
                SELECT COUNT(*) FROM receive_payments rp
                WHERE {where_clause}
            """  # nosec B608
            total = await conn.fetchval(count_query, *params)

            # Get items with invoice count
            query = f"""
                SELECT
                    rp.id, rp.payment_number, rp.customer_id, rp.customer_name,
                    rp.payment_date, rp.payment_method, rp.source_type,
                    rp.total_amount, rp.allocated_amount, rp.unapplied_amount,
                    rp.status, rp.created_at,
                    (SELECT COUNT(*) FROM receive_payment_allocations WHERE payment_id = rp.id) as invoice_count
                FROM receive_payments rp
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """  # nosec B608
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "payment_number": row["payment_number"],
                    "customer_id": str(row["customer_id"])
                    if row["customer_id"]
                    else None,
                    "customer_name": row["customer_name"],
                    "payment_date": row["payment_date"].isoformat(),
                    "payment_method": row["payment_method"],
                    "source_type": row["source_type"],
                    "total_amount": row["total_amount"],
                    "allocated_amount": row["allocated_amount"] or 0,
                    "unapplied_amount": row["unapplied_amount"] or 0,
                    "status": row["status"],
                    "invoice_count": row["invoice_count"] or 0,
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing receive payments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list receive payments")


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=ReceivePaymentSummaryResponse)
async def get_receive_payments_summary(request: Request):
    """Get summary statistics for receive payments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'voided') as voided_count,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'posted'), 0) as total_received,
                    COALESCE(SUM(allocated_amount) FILTER (WHERE status = 'posted'), 0) as total_allocated,
                    COALESCE(SUM(unapplied_amount) FILTER (WHERE status = 'posted'), 0) as total_unapplied
                FROM receive_payments
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "voided_count": row["voided_count"] or 0,
                    "total_received": int(row["total_received"] or 0),
                    "total_allocated": int(row["total_allocated"] or 0),
                    "total_unapplied": int(row["total_unapplied"] or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting receive payments summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET RECEIVE PAYMENT DETAIL
# =============================================================================


@router.get("/{payment_id}", response_model=ReceivePaymentDetailResponse)
async def get_receive_payment(request: Request, payment_id: UUID):
    """Get detailed information for a receive payment."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

            # Get payment with related data
            payment = await conn.fetchrow(
                """
                SELECT rp.*,
                       sd.deposit_number as source_deposit_number,
                       cd.deposit_number as created_deposit_number
                FROM receive_payments rp
                LEFT JOIN customer_deposits sd ON rp.source_deposit_id = sd.id
                LEFT JOIN customer_deposits cd ON rp.created_deposit_id = cd.id
                WHERE rp.id = $1 AND rp.tenant_id = $2
            """,
                payment_id,
                ctx["tenant_id"],
            )

            if not payment:
                raise HTTPException(status_code=404, detail="Receive payment not found")

            # Get allocations
            allocations = await conn.fetch(
                """
                SELECT * FROM receive_payment_allocations
                WHERE payment_id = $1
                ORDER BY created_at
            """,
                payment_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(payment["id"]),
                    "payment_number": payment["payment_number"],
                    "customer_id": str(payment["customer_id"])
                    if payment["customer_id"]
                    else None,
                    "customer_name": payment["customer_name"],
                    "payment_date": payment["payment_date"].isoformat(),
                    "payment_method": payment["payment_method"],
                    "bank_account_id": str(payment["bank_account_id"]),
                    "bank_account_name": payment["bank_account_name"],
                    "source_type": payment["source_type"],
                    "source_deposit_id": str(payment["source_deposit_id"])
                    if payment["source_deposit_id"]
                    else None,
                    "source_deposit_number": payment["source_deposit_number"],
                    "total_amount": payment["total_amount"],
                    "allocated_amount": payment["allocated_amount"] or 0,
                    "unapplied_amount": payment["unapplied_amount"] or 0,
                    "discount_amount": payment["discount_amount"] or 0,
                    "discount_account_id": str(payment["discount_account_id"])
                    if payment["discount_account_id"]
                    else None,
                    "status": payment["status"],
                    "reference_number": payment["reference_number"],
                    "notes": payment["notes"],
                    "journal_id": str(payment["journal_id"])
                    if payment["journal_id"]
                    else None,
                    "journal_number": payment["journal_number"],
                    "void_journal_id": str(payment["void_journal_id"])
                    if payment["void_journal_id"]
                    else None,
                    "created_deposit_id": str(payment["created_deposit_id"])
                    if payment["created_deposit_id"]
                    else None,
                    "created_deposit_number": payment["created_deposit_number"],
                    "allocations": [
                        {
                            "id": str(alloc["id"]),
                            "invoice_id": str(alloc["invoice_id"]),
                            "invoice_number": alloc["invoice_number"],
                            "invoice_amount": alloc["invoice_amount"],
                            "remaining_before": alloc["remaining_before"],
                            "amount_applied": alloc["amount_applied"],
                            "remaining_after": alloc["remaining_after"],
                        }
                        for alloc in allocations
                    ],
                    "posted_at": payment["posted_at"].isoformat()
                    if payment["posted_at"]
                    else None,
                    "posted_by": str(payment["posted_by"])
                    if payment["posted_by"]
                    else None,
                    "voided_at": payment["voided_at"].isoformat()
                    if payment["voided_at"]
                    else None,
                    "voided_by": str(payment["voided_by"])
                    if payment["voided_by"]
                    else None,
                    "void_reason": payment["void_reason"],
                    "created_at": payment["created_at"].isoformat(),
                    "updated_at": payment["updated_at"].isoformat(),
                    "created_by": str(payment["created_by"])
                    if payment["created_by"]
                    else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get receive payment")


# =============================================================================
# CREATE RECEIVE PAYMENT
# =============================================================================

@router.post("", response_model=ReceivePaymentResponse, status_code=201)
async def create_receive_payment(request: Request, body: CreateReceivePaymentRequest):
    """
    Create a new receive payment.

    If save_as_draft=True, saves as draft without posting.
    If save_as_draft=False (default), posts immediately with journal entry.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

                # Validate bank account exists and is asset type
                bank_account = await conn.fetchrow("""
                    SELECT id, account_code, name, account_type
                    FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.bank_account_id), ctx["tenant_id"])

                if not bank_account:
                    raise HTTPException(status_code=400, detail="Bank account not found")

                if bank_account["account_type"] != "ASSET":
                    raise HTTPException(
                        status_code=400,
                        detail="Bank account must be an asset account (Kas/Bank)"
                    )

                # Validate customer exists
                customer = await conn.fetchrow("""
                    SELECT id, name FROM customers
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.customer_id), ctx["tenant_id"])

                if not customer:
                    raise HTTPException(status_code=400, detail="Customer not found")

                # Validate source deposit if source_type='deposit'
                if body.source_type == "deposit":
                    deposit = await conn.fetchrow("""
                        SELECT id, deposit_number, amount, amount_applied, amount_refunded, status
                        FROM customer_deposits
                        WHERE id = $1 AND tenant_id = $2 AND customer_id = $3
                    """, UUID(body.source_deposit_id), ctx["tenant_id"], UUID(body.customer_id))

                    if not deposit:
                        raise HTTPException(status_code=400, detail="Source deposit not found")

                    if deposit["status"] not in ("posted", "partial"):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Cannot use deposit with status '{deposit['status']}'"
                        )

                    deposit_remaining = deposit["amount"] - (deposit["amount_applied"] or 0) - (deposit["amount_refunded"] or 0)
                    if body.total_amount > deposit_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Payment amount ({body.total_amount}) exceeds deposit remaining ({deposit_remaining})"
                        )

                # Validate allocations
                total_allocated = 0
                validated_allocations = []

                for alloc in body.allocations:
                    invoice = await conn.fetchrow("""
                        SELECT id, invoice_number, total_amount, amount_paid, status, customer_id
                        FROM sales_invoices
                        WHERE id = $1 AND tenant_id = $2
                    """, UUID(alloc.invoice_id), ctx["tenant_id"])

                    if not invoice:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {alloc.invoice_id} not found"
                        )

                    if str(invoice["customer_id"]) != body.customer_id:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {invoice['invoice_number']} belongs to different customer"
                        )

                    invoice_remaining = invoice["total_amount"] - (invoice["amount_paid"] or 0)
                    if alloc.amount_applied > invoice_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Allocation ({alloc.amount_applied}) exceeds invoice remaining ({invoice_remaining})"
                        )

                    validated_allocations.append({
                        "invoice_id": invoice["id"],
                        "invoice_number": invoice["invoice_number"],
                        "invoice_amount": invoice["total_amount"],
                        "remaining_before": invoice_remaining,
                        "amount_applied": alloc.amount_applied,
                        "remaining_after": invoice_remaining - alloc.amount_applied,
                    })
                    total_allocated += alloc.amount_applied

                # Calculate amounts
                allocated_amount = total_allocated
                # Effective amount after discount
                effective_amount = body.total_amount + body.discount_amount
                unapplied_amount = effective_amount - allocated_amount

                if unapplied_amount < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Total allocation ({allocated_amount}) exceeds payment amount ({effective_amount})"
                    )

                # Generate payment number
                payment_number = await conn.fetchval(
                    "SELECT generate_receive_payment_number($1)",
                    ctx["tenant_id"]
                )

                # Insert payment
                payment_id = await conn.fetchval("""
                    INSERT INTO receive_payments (
                        tenant_id, payment_number, customer_id, customer_name,
                        payment_date, payment_method, bank_account_id, bank_account_name,
                        source_type, source_deposit_id,
                        total_amount, allocated_amount, unapplied_amount,
                        discount_amount, discount_account_id,
                        reference_number, notes, status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, 'draft', $18)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    payment_number,
                    UUID(body.customer_id),
                    body.customer_name,
                    body.payment_date,
                    body.payment_method,
                    UUID(body.bank_account_id),
                    body.bank_account_name,
                    body.source_type,
                    UUID(body.source_deposit_id) if body.source_deposit_id else None,
                    body.total_amount,
                    allocated_amount,
                    unapplied_amount,
                    body.discount_amount,
                    UUID(body.discount_account_id) if body.discount_account_id else None,
                    body.reference_number,
                    body.notes,
                    ctx["user_id"]
                )

                # Insert allocations
                for alloc in validated_allocations:
                    await conn.execute("""
                        INSERT INTO receive_payment_allocations (
                            tenant_id, payment_id, invoice_id, invoice_number,
                            invoice_amount, remaining_before, amount_applied, remaining_after
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                        ctx["tenant_id"],
                        payment_id,
                        alloc["invoice_id"],
                        alloc["invoice_number"],
                        alloc["invoice_amount"],
                        alloc["remaining_before"],
                        alloc["amount_applied"],
                        alloc["remaining_after"]
                    )

                logger.info(f"Receive payment created: {payment_id}, number={payment_number}")

                result = {
                    "success": True,
                    "message": "Receive payment created successfully",
                    "data": {
                        "id": str(payment_id),
                        "payment_number": payment_number,
                        "total_amount": body.total_amount,
                        "allocated_amount": allocated_amount,
                        "unapplied_amount": unapplied_amount,
                        "status": "draft"
                    }
                }

                # Auto post if not draft
                if not body.save_as_draft:
                    post_result = await _post_payment(conn, ctx, payment_id)
                    result["data"]["status"] = "posted"
                    result["data"]["journal_id"] = post_result.get("journal_id")
                    result["message"] = "Receive payment created and posted"

                return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating receive payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create receive payment")


# =============================================================================
# UPDATE RECEIVE PAYMENT (DRAFT ONLY)
# =============================================================================

@router.put("/{payment_id}", response_model=ReceivePaymentResponse)
async def update_receive_payment(request: Request, payment_id: UUID, body: UpdateReceivePaymentRequest):
    """
    Update a draft receive payment.

    Only draft payments can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

                # Check payment exists and is draft
                payment = await conn.fetchrow("""
                    SELECT id, status FROM receive_payments
                    WHERE id = $1 AND tenant_id = $2
                """, payment_id, ctx["tenant_id"])

                if not payment:
                    raise HTTPException(status_code=404, detail="Receive payment not found")

                if payment["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft payments can be updated"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True, exclude={'allocations'})

                if not update_data and body.allocations is None:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(payment_id)}
                    }

                # Handle allocations update
                if body.allocations is not None:
                    # Delete existing allocations
                    await conn.execute(
                        "DELETE FROM receive_payment_allocations WHERE payment_id = $1",
                        payment_id
                    )

                    # Get customer_id for validation
                    customer_id = update_data.get("customer_id")
                    if not customer_id:
                        existing = await conn.fetchrow(
                            "SELECT customer_id FROM receive_payments WHERE id = $1",
                            payment_id
                        )
                        customer_id = str(existing["customer_id"])

                    # Validate and insert new allocations
                    total_allocated = 0
                    for alloc in body.allocations:
                        invoice = await conn.fetchrow("""
                            SELECT id, invoice_number, total_amount, amount_paid, customer_id
                            FROM sales_invoices
                            WHERE id = $1 AND tenant_id = $2
                        """, UUID(alloc.invoice_id), ctx["tenant_id"])

                        if not invoice:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invoice {alloc.invoice_id} not found"
                            )

                        if str(invoice["customer_id"]) != customer_id:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invoice belongs to different customer"
                            )

                        invoice_remaining = invoice["total_amount"] - (invoice["amount_paid"] or 0)
                        if alloc.amount_applied > invoice_remaining:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Allocation exceeds invoice remaining"
                            )

                        await conn.execute("""
                            INSERT INTO receive_payment_allocations (
                                tenant_id, payment_id, invoice_id, invoice_number,
                                invoice_amount, remaining_before, amount_applied, remaining_after
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                            ctx["tenant_id"],
                            payment_id,
                            invoice["id"],
                            invoice["invoice_number"],
                            invoice["total_amount"],
                            invoice_remaining,
                            alloc.amount_applied,
                            invoice_remaining - alloc.amount_applied
                        )
                        total_allocated += alloc.amount_applied

                    update_data["allocated_amount"] = total_allocated

                # Recalculate unapplied if total or allocated changed
                if "total_amount" in update_data or "allocated_amount" in update_data or "discount_amount" in update_data:
                    current = await conn.fetchrow(
                        "SELECT total_amount, allocated_amount, discount_amount FROM receive_payments WHERE id = $1",
                        payment_id
                    )
                    total = update_data.get("total_amount", current["total_amount"])
                    discount = update_data.get("discount_amount", current["discount_amount"] or 0)
                    allocated = update_data.get("allocated_amount", current["allocated_amount"] or 0)
                    update_data["unapplied_amount"] = (total + discount) - allocated

                # Build update query
                if update_data:
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field in ("customer_id", "bank_account_id", "discount_account_id", "source_deposit_id") and value:
                            updates.append(f"{field} = ${param_idx}")
                            params.append(UUID(value))
                        else:
                            updates.append(f"{field} = ${param_idx}")
                            params.append(value)
                        param_idx += 1

                    params.extend([payment_id, ctx["tenant_id"]])
                    query = f"""
                        UPDATE receive_payments
                        SET {', '.join(updates)}, updated_at = NOW()
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """  # nosec B608
                    await conn.execute(query, *params)

                logger.info(f"Receive payment updated: {payment_id}")

                return {
                    "success": True,
                    "message": "Receive payment updated successfully",
                    "data": {"id": str(payment_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update receive payment")


# =============================================================================
# DELETE RECEIVE PAYMENT (DRAFT ONLY)
# =============================================================================

@router.delete("/{payment_id}", response_model=ReceivePaymentResponse)
async def delete_receive_payment(request: Request, payment_id: UUID):
    """
    Delete a draft receive payment.

    Only draft payments can be deleted. Use void for posted payments.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

            # Check payment exists and is draft
            payment = await conn.fetchrow("""
                SELECT id, status, payment_number FROM receive_payments
                WHERE id = $1 AND tenant_id = $2
            """, payment_id, ctx["tenant_id"])

            if not payment:
                raise HTTPException(status_code=404, detail="Receive payment not found")

            if payment["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft payments can be deleted. Use void for posted."
                )

            # Delete (cascade will delete allocations)
            await conn.execute(
                "DELETE FROM receive_payments WHERE id = $1",
                payment_id
            )

            logger.info(f"Receive payment deleted: {payment_id}")

            return {
                "success": True,
                "message": "Receive payment deleted successfully",
                "data": {
                    "id": str(payment_id),
                    "payment_number": payment["payment_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete receive payment")
