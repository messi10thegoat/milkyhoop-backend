"""
Bank Reconciliation Router
Reconcile bank transactions with bank statements.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
import asyncpg
import logging
import uuid as uuid_module

from ..config import settings
from ..schemas.bank_reconciliation import (
    StartReconciliationRequest,
    MatchTransactionsRequest,
    UnmatchTransactionsRequest,
    ReconciliationListResponse,
    ReconciliationDetailResponse,
    ReconciliationResponse,
    ReconciliationSummaryResponse,
    UnreconciledTransactionsResponse,
    ReconciliationListItem,
    ReconciliationDetail,
    ReconciliationItem,
    ReconciliationSummary,
    UnreconciledTransaction,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=60
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {
        "tenant_id": tenant_id,
        "user_id": uuid_module.UUID(user_id) if user_id else None,
    }


# ============================================================================
# LIST & DETAIL ENDPOINTS
# ============================================================================


@router.get("", response_model=ReconciliationListResponse)
async def list_reconciliations(
    request: Request,
    bank_account_id: Optional[str] = Query(None),
    status: Literal["all", "in_progress", "completed", "void"] = Query("all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List bank reconciliations."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["br.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2

            if bank_account_id:
                conditions.append(f"br.bank_account_id = ${idx}")
                params.append(uuid_module.UUID(bank_account_id))
                idx += 1

            if status != "all":
                conditions.append(f"br.status = ${idx}")
                params.append(status)
                idx += 1

            where_clause = " AND ".join(conditions)
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bank_reconciliations br WHERE {where_clause}",
                *params,
            )

            query = f"""
                SELECT br.*, ba.account_name
                FROM bank_reconciliations br
                JOIN bank_accounts ba ON br.bank_account_id = ba.id
                WHERE {where_clause}
                ORDER BY br.created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            return ReconciliationListResponse(
                items=[
                    ReconciliationListItem(
                        id=str(r["id"]),
                        reconciliation_number=r["reconciliation_number"],
                        bank_account_id=str(r["bank_account_id"]),
                        bank_account_name=r["account_name"],
                        statement_date=r["statement_date"].isoformat(),
                        statement_closing_balance=r["statement_closing_balance"],
                        system_closing_balance=r["system_closing_balance"],
                        difference=r["difference"],
                        status=r["status"],
                        created_at=r["created_at"].isoformat(),
                        completed_at=r["completed_at"].isoformat()
                        if r["completed_at"]
                        else None,
                    )
                    for r in rows
                ],
                total=total,
                has_more=(skip + limit) < total,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing reconciliations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list reconciliations")


@router.get("/{recon_id}", response_model=ReconciliationDetailResponse)
async def get_reconciliation_detail(request: Request, recon_id: str):
    """Get reconciliation detail with items."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            recon = await conn.fetchrow(
                """
                SELECT br.*, ba.account_name
                FROM bank_reconciliations br
                JOIN bank_accounts ba ON br.bank_account_id = ba.id
                WHERE br.id = $1 AND br.tenant_id = $2
            """,
                uuid_module.UUID(recon_id),
                ctx["tenant_id"],
            )

            if not recon:
                raise HTTPException(status_code=404, detail="Reconciliation not found")

            items = await conn.fetch(
                """
                SELECT bri.*, bt.transaction_date, bt.transaction_type, bt.amount, bt.description
                FROM bank_reconciliation_items bri
                JOIN bank_transactions bt ON bri.bank_transaction_id = bt.id
                WHERE bri.reconciliation_id = $1
                ORDER BY bt.transaction_date
            """,
                uuid_module.UUID(recon_id),
            )

            is_balanced = abs(recon["difference"]) < 1

            return ReconciliationDetailResponse(
                success=True,
                data=ReconciliationDetail(
                    id=str(recon["id"]),
                    reconciliation_number=recon["reconciliation_number"],
                    bank_account_id=str(recon["bank_account_id"]),
                    bank_account_name=recon["account_name"],
                    statement_date=recon["statement_date"].isoformat(),
                    statement_start_date=recon["statement_start_date"].isoformat(),
                    statement_end_date=recon["statement_end_date"].isoformat(),
                    statement_opening_balance=recon["statement_opening_balance"],
                    statement_closing_balance=recon["statement_closing_balance"],
                    system_opening_balance=recon["system_opening_balance"],
                    system_closing_balance=recon["system_closing_balance"],
                    reconciled_deposits=recon["reconciled_deposits"],
                    reconciled_withdrawals=recon["reconciled_withdrawals"],
                    unreconciled_deposits=recon["unreconciled_deposits"],
                    unreconciled_withdrawals=recon["unreconciled_withdrawals"],
                    difference=recon["difference"],
                    is_balanced=is_balanced,
                    status=recon["status"],
                    items=[
                        ReconciliationItem(
                            id=str(i["id"]),
                            bank_transaction_id=str(i["bank_transaction_id"]),
                            transaction_date=i["transaction_date"].isoformat(),
                            transaction_type=i["transaction_type"],
                            amount=i["amount"],
                            description=i["description"],
                            is_matched=i["is_matched"],
                            matched_at=i["matched_at"].isoformat()
                            if i["matched_at"]
                            else None,
                            adjustment_amount=i["adjustment_amount"] or 0,
                        )
                        for i in items
                    ],
                    created_at=recon["created_at"].isoformat(),
                    updated_at=recon["updated_at"].isoformat(),
                    completed_at=recon["completed_at"].isoformat()
                    if recon["completed_at"]
                    else None,
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting reconciliation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get reconciliation")


# ============================================================================
# CREATE, UPDATE, DELETE ENDPOINTS
# ============================================================================


@router.post("", response_model=ReconciliationResponse)
async def start_reconciliation(request: Request, body: StartReconciliationRequest):
    """Start a new bank reconciliation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Validate bank account
                bank = await conn.fetchrow(
                    """
                    SELECT id, current_balance FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(body.bank_account_id),
                    ctx["tenant_id"],
                )

                if not bank:
                    raise HTTPException(
                        status_code=404, detail="Bank account not found"
                    )

                # Check for existing in_progress reconciliation
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM bank_reconciliations
                    WHERE bank_account_id = $1 AND status = 'in_progress'
                """,
                    uuid_module.UUID(body.bank_account_id),
                )

                if existing:
                    raise HTTPException(
                        status_code=400,
                        detail="Bank account has an in-progress reconciliation",
                    )

                recon_number = await conn.fetchval(
                    "SELECT generate_reconciliation_number($1, 'REC')", ctx["tenant_id"]
                )

                system_balance = bank["current_balance"]
                recon_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO bank_reconciliations (
                        id, tenant_id, bank_account_id, reconciliation_number,
                        statement_date, statement_start_date, statement_end_date,
                        statement_opening_balance, statement_closing_balance,
                        system_opening_balance, system_closing_balance,
                        status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, 'in_progress', $11)
                """,
                    recon_id,
                    ctx["tenant_id"],
                    uuid_module.UUID(body.bank_account_id),
                    recon_number,
                    body.statement_date,
                    body.statement_start_date,
                    body.statement_end_date,
                    body.statement_opening_balance,
                    body.statement_closing_balance,
                    system_balance,
                    ctx["user_id"],
                )

                # Add unreconciled transactions in period to items
                transactions = await conn.fetch(
                    """
                    SELECT id FROM bank_transactions
                    WHERE bank_account_id = $1
                    AND transaction_date BETWEEN $2 AND $3
                    AND is_reconciled = false
                """,
                    uuid_module.UUID(body.bank_account_id),
                    body.statement_start_date,
                    body.statement_end_date,
                )

                for txn in transactions:
                    await conn.execute(
                        """
                        INSERT INTO bank_reconciliation_items (id, reconciliation_id, bank_transaction_id)
                        VALUES ($1, $2, $3)
                    """,
                        uuid_module.uuid4(),
                        recon_id,
                        txn["id"],
                    )

                return ReconciliationResponse(
                    success=True,
                    message="Reconciliation started",
                    data={"id": str(recon_id), "reconciliation_number": recon_number},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting reconciliation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start reconciliation")


@router.delete("/{recon_id}", response_model=ReconciliationResponse)
async def delete_reconciliation(request: Request, recon_id: str):
    """Delete a reconciliation (in_progress only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            recon = await conn.fetchrow(
                """
                SELECT id, status, reconciliation_number FROM bank_reconciliations
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(recon_id),
                ctx["tenant_id"],
            )

            if not recon:
                raise HTTPException(status_code=404, detail="Reconciliation not found")

            if recon["status"] != "in_progress":
                raise HTTPException(
                    status_code=400,
                    detail="Only in-progress reconciliations can be deleted",
                )

            await conn.execute(
                "DELETE FROM bank_reconciliations WHERE id = $1",
                uuid_module.UUID(recon_id),
            )

            return ReconciliationResponse(
                success=True,
                message="Reconciliation deleted",
                data={"reconciliation_number": recon["reconciliation_number"]},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting reconciliation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete reconciliation")


# ============================================================================
# MATCHING ENDPOINTS
# ============================================================================


@router.post("/{recon_id}/match", response_model=ReconciliationResponse)
async def match_transactions(
    request: Request, recon_id: str, body: MatchTransactionsRequest
):
    """Match transactions as reconciled."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                recon = await conn.fetchrow(
                    """
                    SELECT id, status FROM bank_reconciliations
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(recon_id),
                    ctx["tenant_id"],
                )

                if not recon:
                    raise HTTPException(
                        status_code=404, detail="Reconciliation not found"
                    )

                if recon["status"] != "in_progress":
                    raise HTTPException(
                        status_code=400, detail="Reconciliation is not in progress"
                    )

                matched_count = 0
                for txn_id in body.transaction_ids:
                    result = await conn.execute(
                        """
                        UPDATE bank_reconciliation_items
                        SET is_matched = true, matched_at = NOW(), matched_by = $3
                        WHERE reconciliation_id = $1 AND bank_transaction_id = $2
                    """,
                        uuid_module.UUID(recon_id),
                        uuid_module.UUID(txn_id),
                        ctx["user_id"],
                    )

                    if result != "UPDATE 0":
                        matched_count += 1

                # Recalculate totals
                await _recalculate_reconciliation(conn, uuid_module.UUID(recon_id))

                return ReconciliationResponse(
                    success=True,
                    message=f"Matched {matched_count} transactions",
                    data={"matched_count": matched_count},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error matching transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to match transactions")


@router.post("/{recon_id}/unmatch", response_model=ReconciliationResponse)
async def unmatch_transactions(
    request: Request, recon_id: str, body: UnmatchTransactionsRequest
):
    """Unmatch previously matched transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                recon = await conn.fetchrow(
                    """
                    SELECT id, status FROM bank_reconciliations
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(recon_id),
                    ctx["tenant_id"],
                )

                if not recon:
                    raise HTTPException(
                        status_code=404, detail="Reconciliation not found"
                    )

                if recon["status"] != "in_progress":
                    raise HTTPException(
                        status_code=400, detail="Reconciliation is not in progress"
                    )

                for txn_id in body.transaction_ids:
                    await conn.execute(
                        """
                        UPDATE bank_reconciliation_items
                        SET is_matched = false, matched_at = NULL, matched_by = NULL
                        WHERE reconciliation_id = $1 AND bank_transaction_id = $2
                    """,
                        uuid_module.UUID(recon_id),
                        uuid_module.UUID(txn_id),
                    )

                await _recalculate_reconciliation(conn, uuid_module.UUID(recon_id))

                return ReconciliationResponse(
                    success=True,
                    message="Transactions unmatched",
                    data={"unmatched_count": len(body.transaction_ids)},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unmatching transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to unmatch transactions")


# ============================================================================
# COMPLETION ENDPOINTS
# ============================================================================


@router.post("/{recon_id}/complete", response_model=ReconciliationResponse)
async def complete_reconciliation(request: Request, recon_id: str):
    """Complete the reconciliation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                recon = await conn.fetchrow(
                    """
                    SELECT br.*, ba.last_reconciled_date, ba.last_reconciled_balance
                    FROM bank_reconciliations br
                    JOIN bank_accounts ba ON br.bank_account_id = ba.id
                    WHERE br.id = $1 AND br.tenant_id = $2
                """,
                    uuid_module.UUID(recon_id),
                    ctx["tenant_id"],
                )

                if not recon:
                    raise HTTPException(
                        status_code=404, detail="Reconciliation not found"
                    )

                if recon["status"] != "in_progress":
                    raise HTTPException(
                        status_code=400, detail="Reconciliation is not in progress"
                    )

                # Update bank_transactions.is_reconciled for matched items
                await conn.execute(
                    """
                    UPDATE bank_transactions bt
                    SET is_reconciled = true, reconciled_at = NOW(), reconciled_by = $2
                    FROM bank_reconciliation_items bri
                    WHERE bt.id = bri.bank_transaction_id
                    AND bri.reconciliation_id = $1
                    AND bri.is_matched = true
                """,
                    uuid_module.UUID(recon_id),
                    ctx["user_id"],
                )

                # Update reconciliation status
                await conn.execute(
                    """
                    UPDATE bank_reconciliations
                    SET status = 'completed', completed_at = NOW(), completed_by = $2
                    WHERE id = $1
                """,
                    uuid_module.UUID(recon_id),
                    ctx["user_id"],
                )

                # Update bank account last reconciled info
                await conn.execute(
                    """
                    UPDATE bank_accounts
                    SET last_reconciled_date = $2, last_reconciled_balance = $3
                    WHERE id = $1
                """,
                    recon["bank_account_id"],
                    recon["statement_date"],
                    recon["statement_closing_balance"],
                )

                return ReconciliationResponse(
                    success=True,
                    message="Reconciliation completed",
                    data={"reconciliation_number": recon["reconciliation_number"]},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing reconciliation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to complete reconciliation")


@router.post("/{recon_id}/void", response_model=ReconciliationResponse)
async def void_reconciliation(request: Request, recon_id: str):
    """Void a completed reconciliation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                recon = await conn.fetchrow(
                    """
                    SELECT id, status, reconciliation_number FROM bank_reconciliations
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(recon_id),
                    ctx["tenant_id"],
                )

                if not recon:
                    raise HTTPException(
                        status_code=404, detail="Reconciliation not found"
                    )

                if recon["status"] != "completed":
                    raise HTTPException(
                        status_code=400,
                        detail="Only completed reconciliations can be voided",
                    )

                # Reset bank_transactions.is_reconciled for items
                await conn.execute(
                    """
                    UPDATE bank_transactions bt
                    SET is_reconciled = false, reconciled_at = NULL, reconciled_by = NULL
                    FROM bank_reconciliation_items bri
                    WHERE bt.id = bri.bank_transaction_id
                    AND bri.reconciliation_id = $1
                """,
                    uuid_module.UUID(recon_id),
                )

                await conn.execute(
                    """
                    UPDATE bank_reconciliations SET status = 'void' WHERE id = $1
                """,
                    uuid_module.UUID(recon_id),
                )

                return ReconciliationResponse(
                    success=True,
                    message="Reconciliation voided",
                    data={"reconciliation_number": recon["reconciliation_number"]},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding reconciliation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void reconciliation")


# ============================================================================
# SUMMARY ENDPOINTS
# ============================================================================


@router.get("/{recon_id}/summary", response_model=ReconciliationSummaryResponse)
async def get_reconciliation_summary(request: Request, recon_id: str):
    """Get reconciliation summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            recon = await conn.fetchrow(
                """
                SELECT * FROM bank_reconciliations WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(recon_id),
                ctx["tenant_id"],
            )

            if not recon:
                raise HTTPException(status_code=404, detail="Reconciliation not found")

            # Count unreconciled
            counts = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE NOT bri.is_matched AND bt.amount > 0) as outstanding_deposits,
                    COUNT(*) FILTER (WHERE NOT bri.is_matched AND bt.amount < 0) as outstanding_withdrawals
                FROM bank_reconciliation_items bri
                JOIN bank_transactions bt ON bri.bank_transaction_id = bt.id
                WHERE bri.reconciliation_id = $1
            """,
                uuid_module.UUID(recon_id),
            )

            is_balanced = abs(recon["difference"]) < 1

            return ReconciliationSummaryResponse(
                success=True,
                data=ReconciliationSummary(
                    statement_closing_balance=recon["statement_closing_balance"],
                    system_closing_balance=recon["system_closing_balance"],
                    reconciled_deposits=recon["reconciled_deposits"],
                    reconciled_withdrawals=recon["reconciled_withdrawals"],
                    unreconciled_deposits=recon["unreconciled_deposits"],
                    unreconciled_withdrawals=recon["unreconciled_withdrawals"],
                    outstanding_deposits_count=counts["outstanding_deposits"],
                    outstanding_withdrawals_count=counts["outstanding_withdrawals"],
                    difference=recon["difference"],
                    is_balanced=is_balanced,
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


@router.get("/{recon_id}/unreconciled", response_model=UnreconciledTransactionsResponse)
async def get_unreconciled_transactions(request: Request, recon_id: str):
    """Get unreconciled transactions for this reconciliation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            recon = await conn.fetchrow(
                """
                SELECT id FROM bank_reconciliations WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(recon_id),
                ctx["tenant_id"],
            )

            if not recon:
                raise HTTPException(status_code=404, detail="Reconciliation not found")

            rows = await conn.fetch(
                """
                SELECT bt.*
                FROM bank_reconciliation_items bri
                JOIN bank_transactions bt ON bri.bank_transaction_id = bt.id
                WHERE bri.reconciliation_id = $1 AND bri.is_matched = false
                ORDER BY bt.transaction_date
            """,
                uuid_module.UUID(recon_id),
            )

            return UnreconciledTransactionsResponse(
                success=True,
                data=[
                    UnreconciledTransaction(
                        id=str(r["id"]),
                        transaction_date=r["transaction_date"].isoformat(),
                        transaction_type=r["transaction_type"],
                        amount=r["amount"],
                        description=r["description"],
                        reference_number=r["reference_number"],
                        is_deposit=r["amount"] > 0,
                    )
                    for r in rows
                ],
                total=len(rows),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting unreconciled: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get unreconciled transactions"
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


async def _recalculate_reconciliation(conn, recon_id):
    """Recalculate reconciliation totals."""
    totals = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(bt.amount) FILTER (WHERE bri.is_matched AND bt.amount > 0), 0) as reconciled_deposits,
            COALESCE(SUM(ABS(bt.amount)) FILTER (WHERE bri.is_matched AND bt.amount < 0), 0) as reconciled_withdrawals,
            COALESCE(SUM(bt.amount) FILTER (WHERE NOT bri.is_matched AND bt.amount > 0), 0) as unreconciled_deposits,
            COALESCE(SUM(ABS(bt.amount)) FILTER (WHERE NOT bri.is_matched AND bt.amount < 0), 0) as unreconciled_withdrawals
        FROM bank_reconciliation_items bri
        JOIN bank_transactions bt ON bri.bank_transaction_id = bt.id
        WHERE bri.reconciliation_id = $1
    """,
        recon_id,
    )

    recon = await conn.fetchrow(
        "SELECT statement_closing_balance, system_closing_balance FROM bank_reconciliations WHERE id = $1",
        recon_id,
    )

    # Calculate difference
    calculated_system = (
        recon["statement_closing_balance"]
        + totals["unreconciled_deposits"]
        - totals["unreconciled_withdrawals"]
    )
    difference = recon["system_closing_balance"] - calculated_system

    await conn.execute(
        """
        UPDATE bank_reconciliations SET
            reconciled_deposits = $2,
            reconciled_withdrawals = $3,
            unreconciled_deposits = $4,
            unreconciled_withdrawals = $5,
            difference = $6
        WHERE id = $1
    """,
        recon_id,
        totals["reconciled_deposits"],
        totals["reconciled_withdrawals"],
        totals["unreconciled_deposits"],
        totals["unreconciled_withdrawals"],
        difference,
    )
