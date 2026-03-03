"""
KasBank Router - Cash & Bank Module API

Migrated 2026-02-13: Balance now derived from journal_entries/journal_lines
instead of denormalized bank_accounts.current_balance.

Each bank_account has a coa_id linking to chart_of_accounts.
Balance = SUM(debit - credit) from journal_lines for that account.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from pydantic import BaseModel
import logging
import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

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
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user_id}


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "kasbank", "balance_source": "journal_entries"}


@router.get("/accounts", response_model=KasBankAccountResponse)
async def list_kasbank_accounts(request: Request):
    """
    Get all cash and bank accounts.
    Balance is derived from journal_entries via coa_id (not denormalized).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    ba.id,
                    ba.account_name,
                    ba.account_number,
                    ba.bank_name,
                    ba.account_type,
                    ba.currency,
                    ba.is_active,
                    ba.created_at,
                    ba.coa_id,
                    COALESCE(jb.journal_balance, 0) as balance
                FROM bank_accounts ba
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit - jl.credit), 0) as journal_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON jl.journal_id = je.id
                    WHERE je.tenant_id = ba.tenant_id
                      AND je.status = 'POSTED'
                      AND jl.account_id = ba.coa_id
                ) jb ON true
                WHERE ba.tenant_id = $1 AND ba.is_active = true
                ORDER BY ba.account_type, ba.account_name ASC
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
                    "balance": row["balance"],
                    "currency": row["currency"] or "IDR",
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in rows
            ]

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


@router.get("/accounts/{account_id}/transactions", response_model=KasBankTransactionsResponse)
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
    Iron Law 1: Amounts derived from journal_lines (ledger supremacy).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            account = await conn.fetchrow(
                """
                SELECT
                    ba.id, ba.account_name, ba.coa_id,
                    COALESCE((
                        SELECT SUM(jl.debit - jl.credit)
                        FROM journal_lines jl
                        JOIN journal_entries je ON jl.journal_id = je.id
                        WHERE je.tenant_id = $2 AND je.status = 'POSTED' AND jl.account_id = ba.coa_id
                    ), 0) as balance
                FROM bank_accounts ba
                WHERE ba.id = $1 AND ba.tenant_id = $2
                """,
                account_id, ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            coa_id = account["coa_id"]

            # Build journal-derived query (Iron Law 1)
            date_filter = ""
            params = [coa_id, ctx["tenant_id"]]
            param_idx = 3

            if start_date:
                date_filter += f" AND je.journal_date >= ${param_idx}::date"
                params.append(start_date)
                param_idx += 1
            if end_date:
                date_filter += f" AND je.journal_date <= ${param_idx}::date"
                params.append(end_date)
                param_idx += 1

            search_filter = ""
            if search:
                search_filter = f" AND (je.source_number ILIKE ${param_idx} OR je.description ILIKE ${param_idx})"
                params.append(f"%{search}%")
                param_idx += 1

            params.extend([limit, offset])

            rows = await conn.fetch(
                f"""
                SELECT
                    je.id::text,
                    je.source_type as type,
                    je.source_number as reference,
                    je.journal_date as date,
                    jl.debit,
                    jl.credit,
                    CASE WHEN jl.debit > 0 THEN 'debit' ELSE 'credit' END as entry_type,
                    COALESCE(je.description, je.source_type) as description,
                    'posted' as status
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                WHERE jl.account_id = $1
                    AND je.tenant_id = $2
                    AND je.status = 'POSTED'
                    AND je.reversed_by_id IS NULL
                    {date_filter}
                    {search_filter}
                ORDER BY je.journal_date DESC, je.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params,
            )

            # Count query (same filters, no LIMIT/OFFSET)
            count_params = params[:-2]  # exclude limit and offset
            count = await conn.fetchval(
                f"""
                SELECT COUNT(*)
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                WHERE jl.account_id = $1
                    AND je.tenant_id = $2
                    AND je.status = 'POSTED'
                    AND je.reversed_by_id IS NULL
                    {date_filter}
                    {search_filter}
                """,
                *count_params,
            )

            transactions = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "reference": row["reference"],
                    "date": row["date"].isoformat() if row["date"] else None,
                    "amount": row["debit"] if row["debit"] > 0 else row["credit"],
                    "entry_type": row["entry_type"],
                    "description": row["description"],
                    "status": row["status"],
                }
                for row in rows
            ]

            return {
                "success": True, "transactions": transactions, "total": count,
                "page": page, "limit": limit, "has_more": offset + limit < count,
                "account": {"id": str(account["id"]), "name": account["account_name"], "balance": account["balance"]},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")


@router.get("/accounts/{account_id}/summary")
async def get_account_summary(
    request: Request,
    account_id: str,
    period: str = Query("month", description="Period: day, week, month, year"),
):
    """Get summary statistics for a specific account. Balance from journal."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            account = await conn.fetchrow(
                """
                SELECT ba.id, ba.account_name, ba.coa_id,
                    COALESCE((
                        SELECT SUM(jl.debit - jl.credit)
                        FROM journal_lines jl
                        JOIN journal_entries je ON jl.journal_id = je.id
                        WHERE je.tenant_id = $2 AND je.status = 'POSTED' AND jl.account_id = ba.coa_id
                    ), 0) as balance
                FROM bank_accounts ba
                WHERE ba.id = $1 AND ba.tenant_id = $2
                """,
                account_id, ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            period_filter = {"day": "1 day", "week": "7 days", "month": "30 days", "year": "365 days"}.get(period, "30 days")

            # Iron Law 1: Derive inflows/outflows from journal_lines
            coa_id = account["coa_id"]
            inflows = await conn.fetchval(
                f"""
                SELECT COALESCE(SUM(jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE jl.account_id = $1
                    AND je.tenant_id = $2
                    AND je.status = 'POSTED'
                    AND je.reversed_by_id IS NULL
                    AND je.journal_date >= CURRENT_DATE - INTERVAL '{period_filter}'
                    AND jl.debit > 0
                """,
                coa_id, ctx["tenant_id"],
            )

            outflows = await conn.fetchval(
                f"""
                SELECT COALESCE(SUM(jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE jl.account_id = $1
                    AND je.tenant_id = $2
                    AND je.status = 'POSTED'
                    AND je.reversed_by_id IS NULL
                    AND je.journal_date >= CURRENT_DATE - INTERVAL '{period_filter}'
                    AND jl.credit > 0
                """,
                coa_id, ctx["tenant_id"],
            )

            return {
                "success": True, "account_id": account_id,
                "account_name": account["account_name"],
                "current_balance": account["balance"],
                "period": period, "inflows": inflows, "outflows": outflows,
                "net_change": inflows - outflows,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


@router.get("/stats")
async def get_kasbank_stats(request: Request):
    """Get overall KasBank dashboard stats. Balances from journal."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            totals = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN ba.account_type = 'cash' THEN jb.balance ELSE 0 END), 0) as cash_total,
                    COALESCE(SUM(CASE WHEN ba.account_type = 'bank' THEN jb.balance ELSE 0 END), 0) as bank_total,
                    COUNT(*) as account_count
                FROM bank_accounts ba
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit - jl.credit), 0) as balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON jl.journal_id = je.id
                    WHERE je.tenant_id = ba.tenant_id AND je.status = 'POSTED' AND jl.account_id = ba.coa_id
                ) jb ON true
                WHERE ba.tenant_id = $1 AND ba.is_active = true
                """,
                ctx["tenant_id"],
            )

            # Iron Law 1: Derive inflows/outflows from journal_lines
            today_in = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1 AND je.status = 'POSTED'
                    AND je.journal_date = CURRENT_DATE
                    AND je.reversed_by_id IS NULL
                    AND coa.account_type IN ('BANK', 'CASH')
                    AND jl.debit > 0
                """,
                ctx["tenant_id"],
            )
            today_out = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1 AND je.status = 'POSTED'
                    AND je.journal_date = CURRENT_DATE
                    AND je.reversed_by_id IS NULL
                    AND coa.account_type IN ('BANK', 'CASH')
                    AND jl.credit > 0
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
