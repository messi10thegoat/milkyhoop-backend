"""
Customer Deposits Router - Uang Muka Pelanggan

Endpoints for managing customer deposits (advance payments).
Customer deposits can be applied to invoices or refunded back.

Flow:
1. Create draft customer deposit
2. Post to accounting (receive deposit, creates journal)
3. Apply to invoice(s) OR issue refund to customer
4. Void if needed (only if unapplied/unrefunded)

Journal Entry on POST (Receive):
    Dr. Kas/Bank (1-10100/1-10200)           amount
        Cr. Uang Muka Pelanggan (2-10400)        amount

Journal Entry on APPLY (to Invoice):
    Dr. Uang Muka Pelanggan (2-10400)        applied_amount
        Cr. Piutang Usaha (1-10300)              applied_amount

Journal Entry on REFUND:
    Dr. Uang Muka Pelanggan (2-10400)        refund_amount
        Cr. Kas/Bank (1-10100/1-10200)           refund_amount

Endpoints:
- GET    /customer-deposits              - List customer deposits
- GET    /customer-deposits/summary      - Summary statistics
- GET    /customer-deposits/{id}         - Get deposit detail
- POST   /customer-deposits              - Create draft deposit
- PATCH  /customer-deposits/{id}         - Update draft deposit
- DELETE /customer-deposits/{id}         - Delete draft deposit
- POST   /customer-deposits/{id}/post    - Post to accounting
- POST   /customer-deposits/{id}/apply   - Apply to invoice(s)
- POST   /customer-deposits/{id}/refund  - Issue refund to customer
- POST   /customer-deposits/{id}/void    - Void deposit
- GET    /customers/{id}/deposits        - List deposits for customer
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import uuid as uuid_module

from ..schemas.customer_deposits import (
    CreateCustomerDepositRequest,
    UpdateCustomerDepositRequest,
    ApplyCustomerDepositRequest,
    RefundCustomerDepositRequest,
    VoidCustomerDepositRequest,
    CustomerDepositResponse,
    CustomerDepositDetailResponse,
    CustomerDepositListResponse,
    CustomerDepositSummaryResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
CUSTOMER_DEPOSIT_ACCOUNT = "2-10400"  # Uang Muka Pelanggan (Liability)
AR_ACCOUNT = "1-10300"               # Piutang Usaha


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
# LIST CUSTOMER DEPOSITS
# =============================================================================

@router.get("", response_model=CustomerDepositListResponse)
async def list_customer_deposits(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "partial", "applied", "void"]] = Query("all"),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by number or customer name"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["deposit_date", "deposit_number", "amount", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List customer deposits with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Set tenant context
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

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
                params.append(customer_id)
                param_idx += 1

            if search:
                conditions.append(
                    f"(deposit_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"deposit_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"deposit_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "deposit_date": "deposit_date",
                "deposit_number": "deposit_number",
                "amount": "amount",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM customer_deposits WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, deposit_number, customer_id, customer_name,
                       deposit_date, amount, amount_applied, amount_refunded,
                       status, payment_method, reference, created_at
                FROM customer_deposits
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "deposit_number": row["deposit_number"],
                    "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"],
                    "deposit_date": row["deposit_date"].isoformat(),
                    "amount": row["amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["amount"] - (row["amount_applied"] or 0) - (row["amount_refunded"] or 0),
                    "status": row["status"],
                    "payment_method": row["payment_method"],
                    "reference": row["reference"],
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
        logger.error(f"Error listing customer deposits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list customer deposits")


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary", response_model=CustomerDepositSummaryResponse)
async def get_customer_deposits_summary(request: Request):
    """Get summary statistics for customer deposits."""
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
                    COUNT(*) FILTER (WHERE status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE status = 'applied') as applied_count,
                    COALESCE(SUM(amount), 0) as total_value,
                    COALESCE(SUM(amount_applied), 0) as total_applied,
                    COALESCE(SUM(amount_refunded), 0) as total_refunded,
                    COALESCE(SUM(amount - COALESCE(amount_applied, 0) - COALESCE(amount_refunded, 0))
                        FILTER (WHERE status IN ('posted', 'partial')), 0) as available_balance
                FROM customer_deposits
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
        logger.error(f"Error getting customer deposits summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET CUSTOMER DEPOSIT DETAIL
# =============================================================================

@router.get("/{deposit_id}", response_model=CustomerDepositDetailResponse)
async def get_customer_deposit(request: Request, deposit_id: UUID):
    """Get detailed information for a customer deposit."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get deposit
            dep = await conn.fetchrow("""
                SELECT d.*,
                       c.account_code, c.name as account_name,
                       b.account_name as bank_account_name,
                       j.journal_number
                FROM customer_deposits d
                LEFT JOIN chart_of_accounts c ON d.account_id = c.id
                LEFT JOIN bank_accounts b ON d.bank_account_id = b.id
                LEFT JOIN journal_entries j ON d.journal_id = j.id
                WHERE d.id = $1 AND d.tenant_id = $2
            """, deposit_id, ctx["tenant_id"])

            if not dep:
                raise HTTPException(status_code=404, detail="Customer deposit not found")

            # Get applications with invoice numbers
            applications = await conn.fetch("""
                SELECT a.*, s.invoice_number
                FROM customer_deposit_applications a
                LEFT JOIN sales_invoices s ON a.invoice_id = s.id
                WHERE a.deposit_id = $1
                ORDER BY a.application_date
            """, deposit_id)

            # Get refunds
            refunds = await conn.fetch("""
                SELECT * FROM customer_deposit_refunds
                WHERE deposit_id = $1
                ORDER BY refund_date
            """, deposit_id)

            # Build response
            remaining = dep["amount"] - (dep["amount_applied"] or 0) - (dep["amount_refunded"] or 0)

            return {
                "success": True,
                "data": {
                    "id": str(dep["id"]),
                    "deposit_number": dep["deposit_number"],
                    "customer_id": dep["customer_id"],
                    "customer_name": dep["customer_name"],
                    "amount": dep["amount"],
                    "amount_applied": dep["amount_applied"] or 0,
                    "amount_refunded": dep["amount_refunded"] or 0,
                    "remaining_amount": remaining,
                    "deposit_date": dep["deposit_date"].isoformat(),
                    "payment_method": dep["payment_method"],
                    "account_id": str(dep["account_id"]) if dep["account_id"] else None,
                    "account_code": dep["account_code"],
                    "account_name": dep["account_name"],
                    "bank_account_id": str(dep["bank_account_id"]) if dep["bank_account_id"] else None,
                    "bank_account_name": dep["bank_account_name"],
                    "reference": dep["reference"],
                    "notes": dep["notes"],
                    "status": dep["status"],
                    "journal_id": str(dep["journal_id"]) if dep["journal_id"] else None,
                    "journal_number": dep["journal_number"],
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
                            "notes": ref["notes"],
                            "created_at": ref["created_at"].isoformat(),
                        }
                        for ref in refunds
                    ],
                    "posted_at": dep["posted_at"].isoformat() if dep["posted_at"] else None,
                    "posted_by": str(dep["posted_by"]) if dep["posted_by"] else None,
                    "voided_at": dep["voided_at"].isoformat() if dep["voided_at"] else None,
                    "voided_reason": dep["voided_reason"],
                    "created_at": dep["created_at"].isoformat(),
                    "updated_at": dep["updated_at"].isoformat(),
                    "created_by": str(dep["created_by"]) if dep["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get customer deposit")


# =============================================================================
# CREATE CUSTOMER DEPOSIT (DRAFT)
# =============================================================================

@router.post("", response_model=CustomerDepositResponse, status_code=201)
async def create_customer_deposit(request: Request, body: CreateCustomerDepositRequest):
    """
    Create a new customer deposit in draft status.

    Draft deposits can be edited before posting.
    If auto_post=True, will immediately post to accounting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Validate account exists and is asset type
                account = await conn.fetchrow("""
                    SELECT id, account_code, account_type FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.account_id), ctx["tenant_id"])

                if not account:
                    raise HTTPException(status_code=400, detail="Payment account not found")

                if account["account_type"] != "ASSET":
                    raise HTTPException(
                        status_code=400,
                        detail="Payment account must be an asset account (Kas/Bank)"
                    )

                # Generate deposit number
                dep_number = await conn.fetchval(
                    "SELECT generate_customer_deposit_number($1, 'DEP')",
                    ctx["tenant_id"]
                )

                # Insert deposit
                dep_id = await conn.fetchval("""
                    INSERT INTO customer_deposits (
                        tenant_id, deposit_number, customer_id, customer_name,
                        amount, deposit_date, payment_method,
                        account_id, bank_account_id, reference, notes,
                        status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    dep_number,
                    body.customer_id,
                    body.customer_name,
                    body.amount,
                    body.deposit_date,
                    body.payment_method,
                    UUID(body.account_id),
                    UUID(body.bank_account_id) if body.bank_account_id else None,
                    body.reference,
                    body.notes,
                    ctx["user_id"]
                )

                logger.info(f"Customer deposit created: {dep_id}, number={dep_number}")

                result = {
                    "success": True,
                    "message": "Customer deposit created successfully",
                    "data": {
                        "id": str(dep_id),
                        "deposit_number": dep_number,
                        "amount": body.amount,
                        "status": "draft"
                    }
                }

                # Auto post if requested
                if body.auto_post:
                    post_result = await _post_deposit(conn, ctx, dep_id)
                    result["data"]["status"] = "posted"
                    result["data"]["journal_id"] = post_result.get("journal_id")
                    result["message"] = "Customer deposit created and posted"

                return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating customer deposit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create customer deposit")


# =============================================================================
# UPDATE CUSTOMER DEPOSIT (DRAFT ONLY)
# =============================================================================

@router.patch("/{deposit_id}", response_model=CustomerDepositResponse)
async def update_customer_deposit(request: Request, deposit_id: UUID, body: UpdateCustomerDepositRequest):
    """
    Update a draft customer deposit.

    Only draft deposits can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Check status
                dep = await conn.fetchrow("""
                    SELECT id, status FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """, deposit_id, ctx["tenant_id"])

                if not dep:
                    raise HTTPException(status_code=404, detail="Customer deposit not found")

                if dep["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft deposits can be updated"
                    )

                # Build update
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(deposit_id)}
                    }

                # Validate account if provided
                if "account_id" in update_data and update_data["account_id"]:
                    account = await conn.fetchrow("""
                        SELECT id, account_type FROM chart_of_accounts
                        WHERE id = $1 AND tenant_id = $2
                    """, UUID(update_data["account_id"]), ctx["tenant_id"])

                    if not account:
                        raise HTTPException(status_code=400, detail="Payment account not found")

                    if account["account_type"] != "ASSET":
                        raise HTTPException(
                            status_code=400,
                            detail="Payment account must be an asset account"
                        )

                # Build update query
                updates = []
                params = []
                param_idx = 1

                for field, value in update_data.items():
                    if field in ("account_id", "bank_account_id") and value:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(UUID(value))
                    else:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(value)
                    param_idx += 1

                params.extend([deposit_id, ctx["tenant_id"]])
                query = f"""
                    UPDATE customer_deposits
                    SET {', '.join(updates)}, updated_at = NOW()
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

                logger.info(f"Customer deposit updated: {deposit_id}")

                return {
                    "success": True,
                    "message": "Customer deposit updated successfully",
                    "data": {"id": str(deposit_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update customer deposit")


# =============================================================================
# DELETE CUSTOMER DEPOSIT (DRAFT ONLY)
# =============================================================================

@router.delete("/{deposit_id}", response_model=CustomerDepositResponse)
async def delete_customer_deposit(request: Request, deposit_id: UUID):
    """
    Delete a draft customer deposit.

    Only draft deposits can be deleted. Use void for posted deposits.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Check status
            dep = await conn.fetchrow("""
                SELECT id, status, deposit_number FROM customer_deposits
                WHERE id = $1 AND tenant_id = $2
            """, deposit_id, ctx["tenant_id"])

            if not dep:
                raise HTTPException(status_code=404, detail="Customer deposit not found")

            if dep["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft deposits can be deleted. Use void for posted."
                )

            # Delete
            await conn.execute(
                "DELETE FROM customer_deposits WHERE id = $1",
                deposit_id
            )

            logger.info(f"Customer deposit deleted: {deposit_id}")

            return {
                "success": True,
                "message": "Customer deposit deleted successfully",
                "data": {
                    "id": str(deposit_id),
                    "deposit_number": dep["deposit_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete customer deposit")


# =============================================================================
# INTERNAL: POST DEPOSIT
# =============================================================================

async def _post_deposit(conn, ctx: dict, deposit_id: UUID) -> dict:
    """Internal function to post a deposit to accounting."""
    # Get deposit
    dep = await conn.fetchrow("""
        SELECT * FROM customer_deposits
        WHERE id = $1 AND tenant_id = $2
    """, deposit_id, ctx["tenant_id"])

    if not dep:
        raise HTTPException(status_code=404, detail="Customer deposit not found")

    if dep["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot post deposit with status '{dep['status']}'"
        )

    # Get deposit liability account
    deposit_account_id = await conn.fetchval("""
        SELECT id FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2
    """, ctx["tenant_id"], CUSTOMER_DEPOSIT_ACCOUNT)

    if not deposit_account_id:
        raise HTTPException(
            status_code=500,
            detail=f"Customer deposit account {CUSTOMER_DEPOSIT_ACCOUNT} not found"
        )

    # Create journal entry
    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()

    journal_number = await conn.fetchval("""
        SELECT get_next_journal_number($1, 'DEP')
    """, ctx["tenant_id"])

    if not journal_number:
        journal_number = f"DEP-{dep['deposit_number']}"

    await conn.execute("""
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            status, total_debit, total_credit, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'CUSTOMER_DEPOSIT', $6, $7, 'POSTED', $8, $8, $9)
    """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        dep["deposit_date"],
        f"Customer Deposit {dep['deposit_number']} - {dep['customer_name']}",
        deposit_id,
        str(trace_id),
        float(dep["amount"]),
        ctx["user_id"]
    )

    # Dr. Cash/Bank
    await conn.execute("""
        INSERT INTO journal_lines (
            id, journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, 1, $3, $4, 0, $5)
    """,
        uuid_module.uuid4(),
        journal_id,
        dep["account_id"],
        float(dep["amount"]),
        f"Terima Uang Muka - {dep['deposit_number']}"
    )

    # Cr. Customer Deposit Liability
    await conn.execute("""
        INSERT INTO journal_lines (
            id, journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, 2, $3, 0, $4, $5)
    """,
        uuid_module.uuid4(),
        journal_id,
        deposit_account_id,
        float(dep["amount"]),
        f"Uang Muka Pelanggan - {dep['customer_name']}"
    )

    # Create bank transaction if bank account specified
    if dep["bank_account_id"]:
        await conn.execute("""
            INSERT INTO bank_transactions (
                tenant_id, bank_account_id, transaction_date, transaction_type,
                amount, reference, description, source_type, source_id
            ) VALUES ($1, $2, $3, 'deposit', $4, $5, $6, 'CUSTOMER_DEPOSIT', $7)
        """,
            ctx["tenant_id"],
            dep["bank_account_id"],
            dep["deposit_date"],
            dep["amount"],
            dep["reference"],
            f"Customer Deposit - {dep['customer_name']}",
            deposit_id
        )

    # Update deposit status
    await conn.execute("""
        UPDATE customer_deposits
        SET status = 'posted', journal_id = $2,
            posted_at = NOW(), posted_by = $3, updated_at = NOW()
        WHERE id = $1
    """, deposit_id, journal_id, ctx["user_id"])

    return {
        "journal_id": str(journal_id),
        "journal_number": journal_number
    }


# =============================================================================
# POST CUSTOMER DEPOSIT TO ACCOUNTING
# =============================================================================

@router.post("/{deposit_id}/post", response_model=CustomerDepositResponse)
async def post_customer_deposit(request: Request, deposit_id: UUID):
    """
    Post customer deposit to accounting.

    Creates journal entry:
    - Dr. Cash/Bank
    - Cr. Customer Deposit Liability

    Changes status from 'draft' to 'posted'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                result = await _post_deposit(conn, ctx, deposit_id)

                logger.info(f"Customer deposit posted: {deposit_id}, journal={result['journal_id']}")

                return {
                    "success": True,
                    "message": "Customer deposit posted to accounting",
                    "data": {
                        "id": str(deposit_id),
                        "journal_id": result["journal_id"],
                        "journal_number": result["journal_number"],
                        "status": "posted"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post customer deposit")


# =============================================================================
# APPLY CUSTOMER DEPOSIT TO INVOICE(S)
# =============================================================================

@router.post("/{deposit_id}/apply", response_model=CustomerDepositResponse)
async def apply_customer_deposit(request: Request, deposit_id: UUID, body: ApplyCustomerDepositRequest):
    """
    Apply customer deposit to one or more invoices.

    Creates journal entry:
    - Dr. Customer Deposit Liability
    - Cr. Accounts Receivable

    Reduces the invoice's outstanding balance.
    Deposit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get deposit
                dep = await conn.fetchrow("""
                    SELECT * FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """, deposit_id, ctx["tenant_id"])

                if not dep:
                    raise HTTPException(status_code=404, detail="Customer deposit not found")

                if dep["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot apply deposit with status '{dep['status']}'"
                    )

                # Calculate remaining
                remaining = dep["amount"] - (dep["amount_applied"] or 0) - (dep["amount_refunded"] or 0)
                total_to_apply = sum(app.amount for app in body.applications)

                if total_to_apply > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Application amount ({total_to_apply}) exceeds remaining balance ({remaining})"
                    )

                application_date = body.application_date or date.today()
                applications_created = []

                # Get account IDs
                deposit_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], CUSTOMER_DEPOSIT_ACCOUNT)

                ar_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], AR_ACCOUNT)

                if not deposit_account_id or not ar_account_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Required accounts not found"
                    )

                for app in body.applications:
                    # Validate invoice
                    invoice = await conn.fetchrow("""
                        SELECT id, customer_id, customer_name, invoice_number,
                               grand_total, amount_paid, status
                        FROM sales_invoices
                        WHERE id = $1 AND tenant_id = $2
                    """, UUID(app.invoice_id), ctx["tenant_id"])

                    if not invoice:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {app.invoice_id} not found"
                        )

                    # Check invoice has balance
                    invoice_remaining = invoice["grand_total"] - (invoice["amount_paid"] or 0)
                    if app.amount > invoice_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Application amount exceeds invoice remaining balance"
                        )

                    # Check for existing application
                    existing = await conn.fetchval("""
                        SELECT id FROM customer_deposit_applications
                        WHERE deposit_id = $1 AND invoice_id = $2
                    """, deposit_id, UUID(app.invoice_id))

                    if existing:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Deposit already applied to invoice {app.invoice_id}"
                        )

                    # Create journal entry for application
                    journal_id = uuid_module.uuid4()
                    trace_id = uuid_module.uuid4()

                    journal_number = await conn.fetchval("""
                        SELECT get_next_journal_number($1, 'DA')
                    """, ctx["tenant_id"]) or f"DA-{dep['deposit_number']}"

                    await conn.execute("""
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'DEPOSIT_APPLICATION', $6, $7, 'POSTED', $8, $8, $9)
                    """,
                        journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        application_date,
                        f"Apply Deposit {dep['deposit_number']} to {invoice['invoice_number']}",
                        deposit_id,
                        str(trace_id),
                        float(app.amount),
                        ctx["user_id"]
                    )

                    # Dr. Customer Deposit Liability
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        deposit_account_id,
                        float(app.amount),
                        f"Aplikasi Uang Muka - {invoice['invoice_number']}"
                    )

                    # Cr. Accounts Receivable
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        ar_account_id,
                        float(app.amount),
                        f"Pelunasan dari Deposit - {dep['deposit_number']}"
                    )

                    # Create application record
                    app_id = uuid_module.uuid4()

                    await conn.execute("""
                        INSERT INTO customer_deposit_applications (
                            id, tenant_id, deposit_id, invoice_id, invoice_number,
                            amount_applied, application_date, journal_id, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                        app_id,
                        ctx["tenant_id"],
                        deposit_id,
                        UUID(app.invoice_id),
                        invoice["invoice_number"],
                        app.amount,
                        application_date,
                        journal_id,
                        ctx["user_id"]
                    )

                    # Update invoice
                    new_amount_paid = (invoice["amount_paid"] or 0) + app.amount
                    new_status = "paid" if new_amount_paid >= invoice["grand_total"] else invoice["status"]

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
                        "invoice_number": invoice["invoice_number"],
                        "amount": app.amount
                    })

                # Deposit status will be updated by trigger
                logger.info(f"Customer deposit applied: {deposit_id}, applications={len(applications_created)}")

                return {
                    "success": True,
                    "message": f"Deposit applied to {len(applications_created)} invoice(s)",
                    "data": {
                        "id": str(deposit_id),
                        "applications": applications_created
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply customer deposit")


# =============================================================================
# REFUND CUSTOMER DEPOSIT
# =============================================================================

@router.post("/{deposit_id}/refund", response_model=CustomerDepositResponse)
async def refund_customer_deposit(request: Request, deposit_id: UUID, body: RefundCustomerDepositRequest):
    """
    Issue refund to customer from deposit.

    Creates journal entry:
    - Dr. Customer Deposit Liability
    - Cr. Cash/Bank

    Deposit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get deposit
                dep = await conn.fetchrow("""
                    SELECT * FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """, deposit_id, ctx["tenant_id"])

                if not dep:
                    raise HTTPException(status_code=404, detail="Customer deposit not found")

                if dep["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot refund deposit with status '{dep['status']}'"
                    )

                # Check remaining
                remaining = dep["amount"] - (dep["amount_applied"] or 0) - (dep["amount_refunded"] or 0)

                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount ({body.amount}) exceeds remaining balance ({remaining})"
                    )

                # Validate account
                account = await conn.fetchrow("""
                    SELECT id, account_code, name, account_type FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.account_id), ctx["tenant_id"])

                if not account:
                    raise HTTPException(status_code=400, detail="Payment account not found")

                if account["account_type"] != "ASSET":
                    raise HTTPException(
                        status_code=400,
                        detail="Payment account must be an asset account"
                    )

                # Get deposit account
                deposit_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], CUSTOMER_DEPOSIT_ACCOUNT)

                # Create refund journal
                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, 'DR')",
                    ctx["tenant_id"]
                ) or f"DR-{dep['deposit_number']}"

                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'DEPOSIT_REFUND', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund Deposit {dep['deposit_number']} - {dep['customer_name']}",
                    deposit_id,
                    str(trace_id),
                    float(body.amount),
                    ctx["user_id"]
                )

                # Dr. Customer Deposit Liability
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    deposit_account_id,
                    float(body.amount),
                    f"Refund Uang Muka - {dep['deposit_number']}"
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
                    f"Bayar Refund - {dep['customer_name']}"
                )

                # Create bank transaction if bank account specified
                if body.bank_account_id:
                    await conn.execute("""
                        INSERT INTO bank_transactions (
                            tenant_id, bank_account_id, transaction_date, transaction_type,
                            amount, reference, description, source_type, source_id
                        ) VALUES ($1, $2, $3, 'withdrawal', $4, $5, $6, 'DEPOSIT_REFUND', $7)
                    """,
                        ctx["tenant_id"],
                        UUID(body.bank_account_id),
                        body.refund_date,
                        body.amount,
                        body.reference,
                        f"Deposit Refund - {dep['customer_name']}",
                        deposit_id
                    )

                # Create refund record
                await conn.execute("""
                    INSERT INTO customer_deposit_refunds (
                        id, tenant_id, deposit_id, amount, refund_date,
                        payment_method, account_id, bank_account_id,
                        reference, notes, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    refund_id,
                    ctx["tenant_id"],
                    deposit_id,
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
                logger.info(f"Customer deposit refund issued: {deposit_id}, amount={body.amount}")

                return {
                    "success": True,
                    "message": "Refund issued successfully",
                    "data": {
                        "id": str(deposit_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refunding customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to refund customer deposit")


# =============================================================================
# VOID CUSTOMER DEPOSIT
# =============================================================================

@router.post("/{deposit_id}/void", response_model=CustomerDepositResponse)
async def void_customer_deposit(request: Request, deposit_id: UUID, body: VoidCustomerDepositRequest):
    """
    Void a customer deposit.

    Creates reversal journal entry.
    Deposit must have no applications or refunds.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get deposit
                dep = await conn.fetchrow("""
                    SELECT * FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """, deposit_id, ctx["tenant_id"])

                if not dep:
                    raise HTTPException(status_code=404, detail="Customer deposit not found")

                if dep["status"] == "void":
                    raise HTTPException(status_code=400, detail="Deposit already voided")

                if dep["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM customer_deposits WHERE id = $1",
                        deposit_id
                    )
                    return {
                        "success": True,
                        "message": "Draft deposit deleted",
                        "data": {"id": str(deposit_id)}
                    }

                # Check for applications or refunds
                if (dep["amount_applied"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void deposit with applications. Reverse applications first."
                    )

                if (dep["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void deposit with refunds. Reverse refunds first."
                    )

                # Create reversal journal if original was posted
                if dep["journal_id"]:
                    reversal_journal_id = uuid_module.uuid4()

                    # Get original journal lines
                    original_lines = await conn.fetch("""
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """, dep["journal_id"])

                    journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RV')",
                        ctx["tenant_id"]
                    ) or f"RV-{dep['deposit_number']}"

                    # Create reversal header
                    await conn.execute("""
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'CUSTOMER_DEPOSIT', $5, $6, 'POSTED', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {dep['deposit_number']} - {dep['customer_name']}",
                        deposit_id,
                        dep["journal_id"],
                        float(dep["amount"]),
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
                            line["credit"],  # Swap
                            line["debit"],   # Swap
                            f"Reversal - {line['memo'] or ''}"
                        )

                    # Mark original journal as reversed
                    await conn.execute("""
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                    """, dep["journal_id"], reversal_journal_id)

                # Reverse bank transaction if exists
                if dep["bank_account_id"]:
                    await conn.execute("""
                        INSERT INTO bank_transactions (
                            tenant_id, bank_account_id, transaction_date, transaction_type,
                            amount, description, source_type, source_id
                        ) VALUES ($1, $2, CURRENT_DATE, 'withdrawal', $3, $4, 'VOID_DEPOSIT', $5)
                    """,
                        ctx["tenant_id"],
                        dep["bank_account_id"],
                        dep["amount"],
                        f"Void Deposit - {dep['deposit_number']}",
                        deposit_id
                    )

                # Update deposit status
                await conn.execute("""
                    UPDATE customer_deposits
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """, deposit_id, ctx["user_id"], body.reason)

                logger.info(f"Customer deposit voided: {deposit_id}")

                return {
                    "success": True,
                    "message": "Customer deposit voided successfully",
                    "data": {
                        "id": str(deposit_id),
                        "status": "void"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void customer deposit")


# =============================================================================
# LIST DEPOSITS FOR CUSTOMER
# =============================================================================

@router.get("/customer/{customer_id}", response_model=CustomerDepositListResponse)
async def list_customer_deposits_by_customer(
    request: Request,
    customer_id: str,
    status: Optional[Literal["all", "posted", "partial"]] = Query("all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List available deposits for a specific customer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Build conditions
            conditions = ["tenant_id = $1", "customer_id = $2"]
            params = [ctx["tenant_id"], customer_id]
            param_idx = 3

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1
            else:
                # Only show deposits with available balance
                conditions.append("status IN ('posted', 'partial')")

            where_clause = " AND ".join(conditions)

            # Count
            count_query = f"SELECT COUNT(*) FROM customer_deposits WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, deposit_number, customer_id, customer_name,
                       deposit_date, amount, amount_applied, amount_refunded,
                       status, payment_method, reference, created_at
                FROM customer_deposits
                WHERE {where_clause}
                ORDER BY deposit_date DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "deposit_number": row["deposit_number"],
                    "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"],
                    "deposit_date": row["deposit_date"].isoformat(),
                    "amount": row["amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["amount"] - (row["amount_applied"] or 0) - (row["amount_refunded"] or 0),
                    "status": row["status"],
                    "payment_method": row["payment_method"],
                    "reference": row["reference"],
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
        logger.error(f"Error listing deposits for customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list customer deposits")
