"""
KasBank Router - Cash & Bank Module API

This router provides endpoints for the KasBank (Kas & Bank) module,
which handles cash and bank account management.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from pydantic import BaseModel
import logging
import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (initialized on first request)
_pool: Optional[asyncpg.Pool] = None


class KasBankAccountResponse(BaseModel):
    success: bool = True
    accounts: list = []


class KasBankTransactionsResponse(BaseModel):
    success: bool = True
    transactions: list = []
    total: int = 0
    page: int = 1
    has_more: bool = False


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

    return {"tenant_id": tenant_id, "user_id": user_id}


# =============================================================================
# HEALTH CHECK
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "kasbank"}


# =============================================================================
# LIST KASBANK ACCOUNTS (Cash & Bank Accounts)
# =============================================================================
@router.get("/accounts", response_model=KasBankAccountResponse)
async def list_kasbank_accounts(request: Request):
    """
    Get all cash and bank accounts for the KasBank module.
    Returns accounts with their current balances.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id, account_name, account_number, bank_name,
                    account_type, current_balance, currency, is_active,
                    created_at, updated_at
                FROM bank_accounts
                WHERE tenant_id = $1 AND is_active = true
                ORDER BY account_type, account_name ASC
            """,
                ctx["tenant_id"],
            )

            accounts = [
                {
                    "id": str(row["id"]),
                    "name": row["account_name"],
                    "account_number": row["account_number"],
                    "bank_name": row["bank_name"],
                    "type": row["account_type"],
                    "balance": row["current_balance"],
                    "currency": row["currency"] or "IDR",
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                }
                for row in rows
            ]

            # Calculate totals by type
            cash_total = sum(a["balance"] or 0 for a in accounts if a["type"] == "cash")
            bank_total = sum(a["balance"] or 0 for a in accounts if a["type"] == "bank")

            return {
                "success": True,
                "accounts": accounts,
                "summary": {
                    "cash_total": cash_total,
                    "bank_total": bank_total,
                    "total_balance": cash_total + bank_total,
                    "account_count": len(accounts),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing kasbank accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list accounts")


# =============================================================================
# GET ACCOUNT TRANSACTIONS
# =============================================================================
@router.get(
    "/accounts/{account_id}/transactions", response_model=KasBankTransactionsResponse
)
async def get_account_transactions(
    request: Request,
    account_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """
    Get transactions for a specific cash/bank account.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            # Verify account exists
            account = await conn.fetchrow(
                "SELECT id, account_name, current_balance FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                account_id,
                ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Build date filter
            date_filter = ""
            params = [ctx["tenant_id"], account_id]
            param_idx = 3

            if start_date:
                date_filter += f" AND date >= ${param_idx}::date"
                params.append(start_date)
                param_idx += 1
            if end_date:
                date_filter += f" AND date <= ${param_idx}::date"
                params.append(end_date)
                param_idx += 1

            search_filter = ""
            if search:
                search_filter = f" AND (reference ILIKE ${param_idx} OR description ILIKE ${param_idx})"
                params.append(f"%{search}%")
                param_idx += 1

            params.extend([limit, offset])

            # Get transactions from multiple sources
            rows = await conn.fetch(
                f"""
                SELECT * FROM (
                    -- Receive Payments (Money In)
                    SELECT
                        id::text,
                        'receive_payment' as type,
                        payment_number as reference,
                        payment_date as date,
                        total_amount as amount,
                        'debit' as entry_type,
                        COALESCE(notes, 'Payment Received') as description,
                        'posted' as status
                    FROM receive_payments
                    WHERE tenant_id = $1
                      AND bank_account_id::text = $2
                      AND status = 'posted'

                    UNION ALL

                    -- Expenses (Money Out)
                    SELECT
                        id::text,
                        'expense' as type,
                        expense_number as reference,
                        expense_date as date,
                        total_amount as amount,
                        'credit' as entry_type,
                        COALESCE(notes, 'Expense') as description,
                        status
                    FROM expenses
                    WHERE tenant_id = $1
                      AND paid_through_id::text = $2
                      AND status = 'posted'

                    UNION ALL

                    -- Transfer Out
                    SELECT
                        id::text,
                        'transfer_out' as type,
                        transfer_number as reference,
                        transfer_date as date,
                        amount,
                        'credit' as entry_type,
                        COALESCE(notes, 'Transfer Out') as description,
                        status
                    FROM bank_transfers
                    WHERE tenant_id = $1
                      AND from_bank_id::text = $2
                      AND status = 'posted'

                    UNION ALL

                    -- Transfer In
                    SELECT
                        id::text,
                        'transfer_in' as type,
                        transfer_number as reference,
                        transfer_date as date,
                        amount,
                        'debit' as entry_type,
                        COALESCE(notes, 'Transfer In') as description,
                        status
                    FROM bank_transfers
                    WHERE tenant_id = $1
                      AND to_bank_id::text = $2
                      AND status = 'posted'

                    UNION ALL

                    -- Bill Payments (Money Out)
                    SELECT
                        bp.id::text,
                        'bill_payment' as type,
                        bp.reference as reference,
                        bp.payment_date as date,
                        bp.amount,
                        'credit' as entry_type,
                        COALESCE(bp.notes, 'Bill Payment') as description,
                        'posted' as status
                    FROM bill_payments bp
                    WHERE bp.tenant_id = $1
                      AND bp.bank_account_id::text = $2

                ) t
                WHERE 1=1 {date_filter} {search_filter}
                ORDER BY date DESC, reference DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
                *params,
            )

            # Get total count
            count_params = [ctx["tenant_id"], account_id]
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT id FROM receive_payments
                    WHERE tenant_id = $1 AND bank_account_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id FROM expenses
                    WHERE tenant_id = $1 AND paid_through_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id FROM bank_transfers
                    WHERE tenant_id = $1 AND from_bank_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id FROM bank_transfers
                    WHERE tenant_id = $1 AND to_bank_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id FROM bill_payments
                    WHERE tenant_id = $1 AND bank_account_id::text = $2
                ) t
            """,
                *count_params,
            )

            transactions = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "reference": row["reference"],
                    "date": row["date"].isoformat() if row["date"] else None,
                    "amount": row["amount"],
                    "entry_type": row["entry_type"],
                    "description": row["description"],
                    "status": row["status"],
                }
                for row in rows
            ]

            return {
                "success": True,
                "transactions": transactions,
                "total": count,
                "page": page,
                "limit": limit,
                "has_more": offset + limit < count,
                "account": {
                    "id": str(account["id"]),
                    "name": account["account_name"],
                    "balance": account["current_balance"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")


# =============================================================================
# GET ACCOUNT SUMMARY
# =============================================================================
@router.get("/accounts/{account_id}/summary")
async def get_account_summary(
    request: Request,
    account_id: str,
    period: str = Query("month", description="Period: day, week, month, year"),
):
    """
    Get summary statistics for a specific account.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            account = await conn.fetchrow(
                "SELECT id, account_name, current_balance FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                account_id,
                ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Get period date range
            period_filter = {
                "day": "1 day",
                "week": "7 days",
                "month": "30 days",
                "year": "365 days",
            }.get(period, "30 days")

            # Get inflows
            inflows = await conn.fetchval(
                f"""
                SELECT COALESCE(SUM(amount), 0) FROM (
                    SELECT total_amount as amount FROM receive_payments
                    WHERE tenant_id = $1 AND bank_account_id::text = $2
                      AND payment_date >= CURRENT_DATE - INTERVAL '{period_filter}' AND status = 'posted'
                    UNION ALL
                    SELECT amount FROM bank_transfers
                    WHERE tenant_id = $1 AND to_bank_id::text = $2
                      AND transfer_date >= CURRENT_DATE - INTERVAL '{period_filter}' AND status = 'posted'
                ) t
            """,
                ctx["tenant_id"],
                account_id,
            )

            # Get outflows
            outflows = await conn.fetchval(
                f"""
                SELECT COALESCE(SUM(amount), 0) FROM (
                    SELECT total_amount as amount FROM expenses
                    WHERE tenant_id = $1 AND paid_through_id::text = $2
                      AND expense_date >= CURRENT_DATE - INTERVAL '{period_filter}' AND status = 'posted'
                    UNION ALL
                    SELECT amount FROM bank_transfers
                    WHERE tenant_id = $1 AND from_bank_id::text = $2
                      AND transfer_date >= CURRENT_DATE - INTERVAL '{period_filter}' AND status = 'posted'
                ) t
            """,
                ctx["tenant_id"],
                account_id,
            )

            return {
                "success": True,
                "account_id": account_id,
                "account_name": account["account_name"],
                "current_balance": account["current_balance"],
                "period": period,
                "inflows": inflows,
                "outflows": outflows,
                "net_change": inflows - outflows,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# KASBANK DASHBOARD STATS
# =============================================================================
@router.get("/stats")
async def get_kasbank_stats(request: Request):
    """
    Get overall KasBank dashboard statistics.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get total balances by type
            totals = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN account_type = 'cash' THEN current_balance ELSE 0 END), 0) as cash_total,
                    COALESCE(SUM(CASE WHEN account_type = 'bank' THEN current_balance ELSE 0 END), 0) as bank_total,
                    COUNT(*) as account_count
                FROM bank_accounts
                WHERE tenant_id = $1 AND is_active = true
            """,
                ctx["tenant_id"],
            )

            # Get today's activity
            today_in = await conn.fetchval(
                """
                SELECT COALESCE(SUM(total_amount), 0) FROM receive_payments
                WHERE tenant_id = $1 AND payment_date = CURRENT_DATE AND status = 'posted'
            """,
                ctx["tenant_id"],
            )

            today_out = await conn.fetchval(
                """
                SELECT COALESCE(SUM(total_amount), 0) FROM expenses
                WHERE tenant_id = $1 AND expense_date = CURRENT_DATE AND status = 'posted'
            """,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "cash_balance": totals["cash_total"],
                "bank_balance": totals["bank_total"],
                "total_balance": totals["cash_total"] + totals["bank_total"],
                "account_count": totals["account_count"],
                "today_inflows": today_in,
                "today_outflows": today_out,
                "today_net": today_in - today_out,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get stats")
