"""
General Ledger Router - Read-Only Ledger Views

Endpoints for viewing account ledgers and balances.
All operations are read-only - ledger is populated via journals.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..schemas.ledger import (
    LedgerEntryResponse,
    AccountInfoResponse,
    AccountLedgerResponse,
    AccountBalanceResponse,
    LedgerAccountSummary,
    LedgerListResponse,
    LedgerSummaryResponse,
    TypeSummary,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


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

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id}


# =============================================================================
# LIST ALL ACCOUNTS WITH BALANCES
# =============================================================================
@router.get("", response_model=LedgerListResponse)
async def list_ledger_accounts(
    request: Request,
    as_of_date: Optional[date] = Query(
        None, description="Balance as of date (default: today)"
    ),
    account_type: Optional[str] = Query(
        None, description="Filter by type: ASSET, LIABILITY, etc."
    ),
    include_zero: bool = Query(False, description="Include accounts with zero balance"),
):
    """List all accounts with their current balances."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["coa.tenant_id = $1", "coa.is_active = TRUE"]
            params = [ctx["tenant_id"], as_of_date]
            param_idx = 3

            if account_type:
                conditions.append(f"coa.account_type = ${param_idx}")
                params.append(account_type.upper())
                param_idx += 1

            where_clause = " AND ".join(conditions)

            having_clause = ""
            if not include_zero:
                having_clause = "HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0"

            # Query uses parameterized placeholders ($1, $2, etc.) - safe from SQL injection
            query = f"""
                SELECT
                    coa.id,
                    coa.account_code as code,
                    coa.name,
                    coa.account_type,
                    coa.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as debit_balance,
                    COALESCE(SUM(jl.credit), 0) as credit_balance,
                    CASE
                        WHEN coa.normal_balance = 'DEBIT'
                        THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
                        ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
                    END as net_balance
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE {where_clause}
                GROUP BY coa.id
                {having_clause}
                ORDER BY coa.account_code
            """  # nosec B608

            rows = await conn.fetch(query, *params)

            accounts = [
                LedgerAccountSummary(
                    id=str(row["id"]),
                    code=row["code"],
                    name=row["name"],
                    account_type=row["account_type"],
                    normal_balance=row["normal_balance"],
                    debit_balance=row["debit_balance"],
                    credit_balance=row["credit_balance"],
                    net_balance=row["net_balance"],
                )
                for row in rows
            ]

            return LedgerListResponse(data=accounts, as_of_date=as_of_date)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List ledger accounts error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list ledger accounts")


# =============================================================================
# GET LEDGER SUMMARY (must be before /{account_id} to avoid route conflict)
# =============================================================================
@router.get("/summary", response_model=LedgerSummaryResponse)
async def get_ledger_summary(
    request: Request,
    as_of_date: Optional[date] = Query(None),
):
    """Get summary of ledger balances by account type."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            rows = await conn.fetch(
                """
                SELECT
                    coa.account_type,
                    COUNT(DISTINCT coa.id) as account_count,
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE coa.tenant_id = $1 AND coa.is_active = TRUE
                GROUP BY coa.account_type
            """,
                ctx["tenant_id"],
                as_of_date,
            )

            by_type = {}
            totals = {
                "ASSET": 0,
                "LIABILITY": 0,
                "EQUITY": 0,
                "INCOME": 0,
                "EXPENSE": 0,
            }

            for row in rows:
                account_type = row["account_type"]
                total_debit = row["total_debit"] or Decimal("0")
                total_credit = row["total_credit"] or Decimal("0")

                # Calculate balance based on normal balance
                if account_type in ("ASSET", "EXPENSE"):
                    balance = total_debit - total_credit
                else:
                    balance = total_credit - total_debit

                by_type[account_type] = TypeSummary(
                    total_debit=total_debit,
                    total_credit=total_credit,
                    balance=balance,
                    account_count=row["account_count"],
                )
                totals[account_type] = balance

            # Accounting equation check: Assets = Liabilities + Equity
            is_balanced = totals["ASSET"] == (
                totals["LIABILITY"]
                + totals["EQUITY"]
                + totals["INCOME"]
                - totals["EXPENSE"]
            )

            return LedgerSummaryResponse(
                data={
                    "by_type": by_type,
                    "total_assets": totals["ASSET"],
                    "total_liabilities": totals["LIABILITY"],
                    "total_equity": totals["EQUITY"],
                    "total_revenue": totals["INCOME"],
                    "total_expenses": totals["EXPENSE"],
                    "is_balanced": is_balanced,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get ledger summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get ledger summary")


# =============================================================================
# GET ACCOUNT LEDGER
# =============================================================================
@router.get("/{account_id}", response_model=AccountLedgerResponse)
async def get_account_ledger(
    request: Request,
    account_id: UUID,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Get detailed ledger for a single account with running balance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get account info
            account = await conn.fetchrow(
                """
                SELECT id, account_code, name, account_type, normal_balance
                FROM chart_of_accounts
                WHERE id = $1 AND tenant_id = $2
            """,
                account_id,
                ctx["tenant_id"],
            )

            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Calculate opening balance (before start_date)
            opening_balance = Decimal("0")
            if start_date:
                ob_row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(jl.debit), 0) as total_debit,
                        COALESCE(SUM(jl.credit), 0) as total_credit
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = $1
                      AND je.tenant_id = $2
                      AND je.status = 'POSTED'
                      AND je.journal_date < $3
                """,
                    account_id,
                    ctx["tenant_id"],
                    start_date,
                )

                if account["normal_balance"] == "DEBIT":
                    opening_balance = (ob_row["total_debit"] or 0) - (
                        ob_row["total_credit"] or 0
                    )
                else:
                    opening_balance = (ob_row["total_credit"] or 0) - (
                        ob_row["total_debit"] or 0
                    )

            # Build query for entries
            conditions = [
                "jl.account_id = $1",
                "je.tenant_id = $2",
                "je.status = 'POSTED'",
            ]
            params = [account_id, ctx["tenant_id"]]
            param_idx = 3

            if start_date:
                conditions.append(f"je.journal_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"je.journal_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            offset = (page - 1) * limit
            params.extend([limit, offset])

            # Query uses parameterized placeholders ($1, $2, etc.) - safe from SQL injection
            entries_query = f"""
                SELECT
                    je.journal_date as date,
                    je.journal_number,
                    je.id as journal_id,
                    je.description,
                    jl.debit,
                    jl.credit,
                    je.source_type
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE {where_clause}
                ORDER BY je.journal_date, je.created_at
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """  # nosec B608

            rows = await conn.fetch(entries_query, *params)

            # Calculate running balance
            running_balance = opening_balance
            entries = []
            total_debit = Decimal("0")
            total_credit = Decimal("0")

            for row in rows:
                debit = row["debit"] or Decimal("0")
                credit = row["credit"] or Decimal("0")

                if account["normal_balance"] == "DEBIT":
                    running_balance = running_balance + debit - credit
                else:
                    running_balance = running_balance + credit - debit

                total_debit += debit
                total_credit += credit

                entries.append(
                    LedgerEntryResponse(
                        date=row["date"],
                        journal_number=row["journal_number"],
                        journal_id=str(row["journal_id"]),
                        description=row["description"],
                        debit=debit,
                        credit=credit,
                        running_balance=running_balance,
                        source_type=row["source_type"].lower()
                        if row["source_type"]
                        else "manual",
                    )
                )

            closing_balance = opening_balance
            if account["normal_balance"] == "DEBIT":
                closing_balance = opening_balance + total_debit - total_credit
            else:
                closing_balance = opening_balance + total_credit - total_debit

            return AccountLedgerResponse(
                data={
                    "account": AccountInfoResponse(
                        id=str(account["id"]),
                        code=account["account_code"],
                        name=account["name"],
                        account_type=account["account_type"],
                        normal_balance=account["normal_balance"],
                    ),
                    "opening_balance": opening_balance,
                    "entries": entries,
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "closing_balance": closing_balance,
                    "net_movement": total_debit - total_credit,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get account ledger error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account ledger")


# =============================================================================
# GET ACCOUNT BALANCE
# =============================================================================
@router.get("/{account_id}/balance", response_model=AccountBalanceResponse)
async def get_account_balance(
    request: Request,
    account_id: UUID,
    as_of_date: Optional[date] = Query(None),
):
    """Get point-in-time balance for an account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow(
                """
                SELECT
                    coa.id,
                    coa.account_code,
                    coa.name,
                    coa.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as debit_balance,
                    COALESCE(SUM(jl.credit), 0) as credit_balance
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $3
                WHERE coa.id = $1 AND coa.tenant_id = $2
                GROUP BY coa.id
            """,
                account_id,
                ctx["tenant_id"],
                as_of_date,
            )

            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            if row["normal_balance"] == "DEBIT":
                net_balance = row["debit_balance"] - row["credit_balance"]
            else:
                net_balance = row["credit_balance"] - row["debit_balance"]

            return AccountBalanceResponse(
                data={
                    "account_id": str(row["id"]),
                    "account_code": row["account_code"],
                    "account_name": row["name"],
                    "as_of_date": as_of_date,
                    "debit_balance": row["debit_balance"],
                    "credit_balance": row["credit_balance"],
                    "net_balance": net_balance,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get account balance error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account balance")
