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
    ReceivePaymentDetailResponse,
    ReceivePaymentListResponse,
    ReceivePaymentSummaryResponse,
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
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

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
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

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
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

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
