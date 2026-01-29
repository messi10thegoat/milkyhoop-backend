"""
Intercompany Router - Intercompany Transaction Management

Manages intercompany transactions between entities within a group,
including reconciliation and balance tracking.

Journal Entries:
- From Entity: Dr. IC Receivable / Cr. IC Sales
- To Entity: Dr. IC Purchases / Cr. IC Payable
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
from datetime import date, timedelta
import logging
import asyncpg

from ..schemas.intercompany import (
    CreateIntercompanyTransactionRequest,
    UpdateIntercompanyTransactionRequest,
    IntercompanyTransactionListResponse,
    IntercompanyTransactionDetailResponse,
    ConfirmTransactionRequest,
    RejectTransactionRequest,
    ReconcileRequest,
    ReconcileResponse,
    UnreconciledListResponse,
    VarianceReportResponse,
    IntercompanyBalanceListResponse,
    IntercompanyBalanceDetailResponse,
    CreateSettlementRequest,
    SettlementListResponse,
    IntercompanyReportResponse,
    IntercompanyAgingResponse,
    IntercompanyResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

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
            command_timeout=60
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
    """Health check endpoint."""
    return {"status": "ok", "service": "intercompany"}


# =============================================================================
# TRANSACTIONS
# =============================================================================
@router.get("/transactions", response_model=IntercompanyTransactionListResponse)
async def list_transactions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    entity_tenant_id: Optional[str] = Query(None, description="Filter by counterparty"),
    transaction_type: Optional[str] = Query(None),
    is_reconciled: Optional[bool] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List intercompany transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = [
                "(ict.tenant_id = $1 OR ict.from_entity_tenant_id = $1 OR ict.to_entity_tenant_id = $1)"
            ]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if entity_tenant_id:
                conditions.append(f"(ict.from_entity_tenant_id = ${param_idx} OR ict.to_entity_tenant_id = ${param_idx})")
                params.append(entity_tenant_id)
                param_idx += 1

            if transaction_type:
                conditions.append(f"ict.transaction_type = ${param_idx}")
                params.append(transaction_type)
                param_idx += 1

            if is_reconciled is not None:
                conditions.append(f"ict.is_reconciled = ${param_idx}")
                params.append(is_reconciled)
                param_idx += 1

            if start_date:
                conditions.append(f"ict.transaction_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"ict.transaction_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM intercompany_transactions ict WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT ict.*, c.code as currency_code
                FROM intercompany_transactions ict
                LEFT JOIN currencies c ON c.id = ict.currency_id
                WHERE {where_clause}
                ORDER BY ict.transaction_date {sort_order}, ict.created_at {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "transaction_number": row["transaction_number"],
                    "transaction_date": row["transaction_date"],
                    "from_entity_tenant_id": row["from_entity_tenant_id"],
                    "from_entity_name": None,  # Could fetch from consolidation_entities
                    "to_entity_tenant_id": row["to_entity_tenant_id"],
                    "to_entity_name": None,
                    "transaction_type": row["transaction_type"],
                    "amount": row["amount"],
                    "currency_code": row["currency_code"],
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "is_reconciled": row["is_reconciled"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing IC transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list transactions")


@router.post("/transactions", response_model=IntercompanyResponse, status_code=201)
async def create_transaction(request: Request, body: CreateIntercompanyTransactionRequest):
    """Create intercompany transaction and generate journal entries."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate transaction number
                tx_number = await conn.fetchval(
                    "SELECT generate_ic_transaction_number($1)",
                    ctx["tenant_id"]
                )

                # Create transaction
                tx_id = await conn.fetchval(
                    """
                    INSERT INTO intercompany_transactions (
                        tenant_id, transaction_number, transaction_date, description,
                        from_entity_tenant_id, to_entity_tenant_id, transaction_type,
                        amount, currency_id, exchange_rate,
                        from_document_type, from_document_id, from_document_number,
                        from_status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'confirmed', $14)
                    RETURNING id
                    """,
                    ctx["tenant_id"], tx_number, body.transaction_date, body.description,
                    body.from_entity_tenant_id, body.to_entity_tenant_id, body.transaction_type,
                    body.amount, body.currency_id, body.exchange_rate,
                    body.from_document_type, body.from_document_id, body.from_document_number,
                    ctx["user_id"]
                )

                # Create journal entry for "from" entity
                # Dr. IC Receivable (1-10900) / Cr. IC Sales (4-10200) or appropriate
                from_journal_id = await create_from_entity_journal(
                    conn, ctx["tenant_id"], tx_id, tx_number, body, ctx["user_id"]
                )

                # Update transaction with journal
                if from_journal_id:
                    await conn.execute(
                        "UPDATE intercompany_transactions SET from_journal_id = $1 WHERE id = $2",
                        from_journal_id, tx_id
                    )

                return {
                    "success": True,
                    "message": "Intercompany transaction created",
                    "data": {
                        "id": str(tx_id),
                        "transaction_number": tx_number,
                        "from_journal_id": str(from_journal_id) if from_journal_id else None
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating IC transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create transaction")


async def create_from_entity_journal(conn, tenant_id, tx_id, tx_number, body, user_id):
    """Create journal entry for the originating entity."""
    try:
        # Get IC accounts
        ic_receivable = await conn.fetchrow(
            "SELECT id, code FROM chart_of_accounts WHERE tenant_id = $1 AND code = '1-10900'",
            tenant_id
        )
        ic_sales = await conn.fetchrow(
            "SELECT id, code FROM chart_of_accounts WHERE tenant_id = $1 AND code = '4-10200'",
            tenant_id
        )

        if not ic_receivable or not ic_sales:
            logger.warning(f"IC accounts not found for tenant {tenant_id}")
            return None

        # Create journal entry
        journal_number = f"JE-IC-{tx_number}"
        journal_id = await conn.fetchval(
            """
            INSERT INTO journal_entries (
                tenant_id, journal_number, entry_date, description,
                source_type, source_id, status, created_by
            ) VALUES ($1, $2, $3, $4, 'intercompany', $5, 'posted', $6)
            RETURNING id
            """,
            tenant_id, journal_number, body.transaction_date,
            f"IC Transaction: {body.description or body.transaction_type}",
            tx_id, user_id
        )

        # Create journal lines
        # Debit IC Receivable
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, $3, 0, $4)
            """,
            journal_id, ic_receivable["id"], body.amount,
            f"IC Receivable from {body.to_entity_tenant_id}"
        )

        # Credit IC Sales/Revenue
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, 0, $3, $4)
            """,
            journal_id, ic_sales["id"], body.amount,
            f"IC {body.transaction_type} to {body.to_entity_tenant_id}"
        )

        return journal_id

    except Exception as e:
        logger.error(f"Error creating from entity journal: {e}")
        return None


@router.get("/transactions/{transaction_id}", response_model=IntercompanyTransactionDetailResponse)
async def get_transaction(request: Request, transaction_id: UUID):
    """Get intercompany transaction detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT ict.*, c.code as currency_code
                FROM intercompany_transactions ict
                LEFT JOIN currencies c ON c.id = ict.currency_id
                WHERE ict.id = $1
                  AND (ict.tenant_id = $2 OR ict.from_entity_tenant_id = $2 OR ict.to_entity_tenant_id = $2)
            """
            row = await conn.fetchrow(query, transaction_id, ctx["tenant_id"])
            if not row:
                raise HTTPException(status_code=404, detail="Transaction not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "transaction_number": row["transaction_number"],
                    "transaction_date": row["transaction_date"],
                    "description": row["description"],
                    "from_entity_tenant_id": row["from_entity_tenant_id"],
                    "from_entity_name": None,
                    "to_entity_tenant_id": row["to_entity_tenant_id"],
                    "to_entity_name": None,
                    "transaction_type": row["transaction_type"],
                    "amount": row["amount"],
                    "currency_id": str(row["currency_id"]) if row["currency_id"] else None,
                    "currency_code": row["currency_code"],
                    "exchange_rate": row["exchange_rate"],
                    "from_document_type": row["from_document_type"],
                    "from_document_id": str(row["from_document_id"]) if row["from_document_id"] else None,
                    "from_document_number": row["from_document_number"],
                    "to_document_type": row["to_document_type"],
                    "to_document_id": str(row["to_document_id"]) if row["to_document_id"] else None,
                    "to_document_number": row["to_document_number"],
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "from_journal_id": str(row["from_journal_id"]) if row["from_journal_id"] else None,
                    "to_journal_id": str(row["to_journal_id"]) if row["to_journal_id"] else None,
                    "is_reconciled": row["is_reconciled"],
                    "reconciled_at": row["reconciled_at"],
                    "variance_amount": row["variance_amount"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting IC transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transaction")


@router.patch("/transactions/{transaction_id}", response_model=IntercompanyResponse)
async def update_transaction(request: Request, transaction_id: UUID, body: UpdateIntercompanyTransactionRequest):
    """Update intercompany transaction (pending only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check transaction exists and is pending
            tx = await conn.fetchrow(
                """
                SELECT * FROM intercompany_transactions
                WHERE id = $1 AND tenant_id = $2
                """,
                transaction_id, ctx["tenant_id"]
            )
            if not tx:
                raise HTTPException(status_code=404, detail="Transaction not found")

            if tx["from_status"] != "pending":
                raise HTTPException(status_code=400, detail="Can only update pending transactions")

            # Build update
            updates = []
            params = []
            param_idx = 1

            update_data = body.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes to update"}

            updates.append("updated_at = NOW()")
            params.append(transaction_id)

            query = f"UPDATE intercompany_transactions SET {', '.join(updates)} WHERE id = ${param_idx}"
            await conn.execute(query, *params)

            return {"success": True, "message": "Transaction updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating IC transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update transaction")


# =============================================================================
# CONFIRM / REJECT
# =============================================================================
@router.post("/transactions/{transaction_id}/confirm", response_model=IntercompanyResponse)
async def confirm_transaction(request: Request, transaction_id: UUID, body: ConfirmTransactionRequest = None):
    """Confirm receipt of IC transaction (by counterparty)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get transaction
                tx = await conn.fetchrow(
                    """
                    SELECT * FROM intercompany_transactions
                    WHERE id = $1 AND to_entity_tenant_id = $2
                    """,
                    transaction_id, ctx["tenant_id"]
                )
                if not tx:
                    raise HTTPException(status_code=404, detail="Transaction not found or not for your entity")

                if tx["to_status"] != "pending":
                    raise HTTPException(status_code=400, detail="Transaction already processed")

                # Update status
                update_fields = ["to_status = 'confirmed'", "updated_at = NOW()"]
                params = [transaction_id]
                param_idx = 2

                if body:
                    if body.to_document_type:
                        update_fields.append(f"to_document_type = ${param_idx}")
                        params.append(body.to_document_type)
                        param_idx += 1
                    if body.to_document_id:
                        update_fields.append(f"to_document_id = ${param_idx}")
                        params.append(body.to_document_id)
                        param_idx += 1
                    if body.to_document_number:
                        update_fields.append(f"to_document_number = ${param_idx}")
                        params.append(body.to_document_number)
                        param_idx += 1

                await conn.execute(
                    f"UPDATE intercompany_transactions SET {', '.join(update_fields)} WHERE id = $1",
                    *params
                )

                # Create journal entry for "to" entity
                # Dr. IC Purchases (5-10400) / Cr. IC Payable (2-10900)
                to_journal_id = await create_to_entity_journal(
                    conn, ctx["tenant_id"], tx, ctx["user_id"]
                )

                if to_journal_id:
                    await conn.execute(
                        "UPDATE intercompany_transactions SET to_journal_id = $1 WHERE id = $2",
                        to_journal_id, transaction_id
                    )

                return {
                    "success": True,
                    "message": "Transaction confirmed",
                    "data": {"to_journal_id": str(to_journal_id) if to_journal_id else None}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming IC transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to confirm transaction")


async def create_to_entity_journal(conn, tenant_id, tx, user_id):
    """Create journal entry for the receiving entity."""
    try:
        # Get IC accounts
        ic_payable = await conn.fetchrow(
            "SELECT id, code FROM chart_of_accounts WHERE tenant_id = $1 AND code = '2-10900'",
            tenant_id
        )
        ic_purchases = await conn.fetchrow(
            "SELECT id, code FROM chart_of_accounts WHERE tenant_id = $1 AND code = '5-10400'",
            tenant_id
        )

        if not ic_payable or not ic_purchases:
            logger.warning(f"IC accounts not found for tenant {tenant_id}")
            return None

        # Create journal entry
        journal_number = f"JE-IC-{tx['transaction_number']}-TO"
        journal_id = await conn.fetchval(
            """
            INSERT INTO journal_entries (
                tenant_id, journal_number, entry_date, description,
                source_type, source_id, status, created_by
            ) VALUES ($1, $2, $3, $4, 'intercompany', $5, 'posted', $6)
            RETURNING id
            """,
            tenant_id, journal_number, tx["transaction_date"],
            f"IC Transaction Receipt: {tx['description'] or tx['transaction_type']}",
            tx["id"], user_id
        )

        # Debit IC Purchases/Expense
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, $3, 0, $4)
            """,
            journal_id, ic_purchases["id"], tx["amount"],
            f"IC {tx['transaction_type']} from {tx['from_entity_tenant_id']}"
        )

        # Credit IC Payable
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, 0, $3, $4)
            """,
            journal_id, ic_payable["id"], tx["amount"],
            f"IC Payable to {tx['from_entity_tenant_id']}"
        )

        return journal_id

    except Exception as e:
        logger.error(f"Error creating to entity journal: {e}")
        return None


@router.post("/transactions/{transaction_id}/reject", response_model=IntercompanyResponse)
async def reject_transaction(request: Request, transaction_id: UUID, body: RejectTransactionRequest):
    """Reject/dispute IC transaction."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE intercompany_transactions
                SET to_status = 'rejected', updated_at = NOW()
                WHERE id = $1 AND to_entity_tenant_id = $2 AND to_status = 'pending'
                """,
                transaction_id, ctx["tenant_id"]
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Transaction not found or already processed")

            # TODO: Could notify originator about rejection

            return {"success": True, "message": f"Transaction rejected: {body.reason}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting IC transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reject transaction")


# =============================================================================
# RECONCILIATION
# =============================================================================
@router.get("/unreconciled", response_model=UnreconciledListResponse)
async def list_unreconciled(
    request: Request,
    entity_tenant_id: Optional[str] = Query(None),
):
    """List unreconciled transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = [
                "(from_entity_tenant_id = $1 OR to_entity_tenant_id = $1)",
                "is_reconciled = false",
                "from_status = 'confirmed'",
                "to_status = 'confirmed'"
            ]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if entity_tenant_id:
                conditions.append(f"(from_entity_tenant_id = ${param_idx} OR to_entity_tenant_id = ${param_idx})")
                params.append(entity_tenant_id)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT *,
                       CURRENT_DATE - transaction_date as days_outstanding
                FROM intercompany_transactions
                WHERE {where_clause}
                ORDER BY transaction_date ASC
            """
            rows = await conn.fetch(query, *params)

            items = []
            total_amount = 0
            for row in rows:
                counterparty = row["to_entity_tenant_id"] if row["from_entity_tenant_id"] == ctx["tenant_id"] else row["from_entity_tenant_id"]
                items.append({
                    "id": str(row["id"]),
                    "transaction_number": row["transaction_number"],
                    "transaction_date": row["transaction_date"],
                    "counterparty_tenant_id": counterparty,
                    "counterparty_name": None,
                    "transaction_type": row["transaction_type"],
                    "amount": row["amount"],
                    "days_outstanding": row["days_outstanding"],
                })
                total_amount += row["amount"]

            return {"items": items, "total": len(items), "total_amount": total_amount}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing unreconciled: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list unreconciled transactions")


@router.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_transactions(request: Request, body: ReconcileRequest):
    """Reconcile batch of transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                reconciled = 0
                total_variance = 0

                for tx_id in body.transaction_ids:
                    # Get transaction
                    tx = await conn.fetchrow(
                        """
                        SELECT * FROM intercompany_transactions
                        WHERE id = $1
                          AND (from_entity_tenant_id = $2 OR to_entity_tenant_id = $2)
                          AND is_reconciled = false
                          AND from_status = 'confirmed'
                          AND to_status = 'confirmed'
                        """,
                        tx_id, ctx["tenant_id"]
                    )
                    if tx:
                        await conn.execute(
                            """
                            UPDATE intercompany_transactions
                            SET is_reconciled = true,
                                reconciled_at = NOW(),
                                reconciled_by = $1,
                                from_status = 'reconciled',
                                to_status = 'reconciled'
                            WHERE id = $2
                            """,
                            ctx["user_id"], tx_id
                        )

                        # Update IC balance
                        # TODO: Law 1 Violation - Direct balance update bypasses journal ledger
                        # This should create a journal entry instead of directly updating balance
                        # See: Iron Laws compliance audit - Law 1 (Ledger Supremacy)
                        await conn.execute(
                            """
                            UPDATE intercompany_balances
                            SET balance = balance - $1,
                                last_reconciled_date = CURRENT_DATE,
                                updated_at = NOW()
                            WHERE tenant_id = $2
                              AND entity_a_tenant_id = $3
                              AND entity_b_tenant_id = $4
                            """,
                            tx["amount"], ctx["tenant_id"],
                            tx["from_entity_tenant_id"], tx["to_entity_tenant_id"]
                        )

                        reconciled += 1

                return {
                    "success": True,
                    "message": f"Reconciled {reconciled} transactions",
                    "reconciled_count": reconciled,
                    "variance_total": total_variance
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reconciling transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reconcile transactions")


@router.get("/variances", response_model=VarianceReportResponse)
async def get_variances(request: Request):
    """Get variance report for IC transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get transactions with variances
            rows = await conn.fetch(
                """
                SELECT * FROM intercompany_transactions
                WHERE (from_entity_tenant_id = $1 OR to_entity_tenant_id = $1)
                  AND variance_amount != 0
                ORDER BY transaction_date DESC
                LIMIT 100
                """,
                ctx["tenant_id"]
            )

            items = []
            total_variance = 0
            for row in rows:
                items.append({
                    "transaction_id": str(row["id"]),
                    "transaction_number": row["transaction_number"],
                    "from_amount": row["amount"],
                    "to_amount": row["amount"] + row["variance_amount"],
                    "variance": row["variance_amount"],
                    "variance_percent": round(row["variance_amount"] / row["amount"] * 100, 2) if row["amount"] else 0
                })
                total_variance += row["variance_amount"]

            return {"success": True, "items": items, "total_variance": total_variance}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting variances: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get variances")


# =============================================================================
# BALANCES
# =============================================================================
@router.get("/balances", response_model=IntercompanyBalanceListResponse)
async def list_balances(request: Request):
    """List all IC balances."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get balances where we are involved
            rows = await conn.fetch(
                """
                SELECT icb.*,
                       (SELECT COUNT(*) FROM intercompany_transactions ict
                        WHERE (ict.from_entity_tenant_id = icb.entity_a_tenant_id
                               AND ict.to_entity_tenant_id = icb.entity_b_tenant_id)
                           OR (ict.from_entity_tenant_id = icb.entity_b_tenant_id
                               AND ict.to_entity_tenant_id = icb.entity_a_tenant_id)
                       ) as tx_count
                FROM intercompany_balances icb
                WHERE icb.entity_a_tenant_id = $1 OR icb.entity_b_tenant_id = $1
                ORDER BY ABS(icb.balance) DESC
                """,
                ctx["tenant_id"]
            )

            items = []
            total_receivable = 0
            total_payable = 0

            for row in rows:
                # Determine counterparty and balance direction
                if row["entity_a_tenant_id"] == ctx["tenant_id"]:
                    counterparty = row["entity_b_tenant_id"]
                    balance = row["balance"]  # positive = they owe us
                else:
                    counterparty = row["entity_a_tenant_id"]
                    balance = -row["balance"]  # flip sign

                items.append({
                    "entity_tenant_id": counterparty,
                    "entity_name": None,
                    "balance": balance,
                    "currency_code": None,
                    "last_transaction_date": row["last_transaction_date"],
                    "last_reconciled_date": row["last_reconciled_date"],
                    "transaction_count": row["tx_count"],
                })

                if balance > 0:
                    total_receivable += balance
                else:
                    total_payable += abs(balance)

            return {
                "success": True,
                "items": items,
                "total_receivable": total_receivable,
                "total_payable": total_payable,
                "net_position": total_receivable - total_payable
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing IC balances: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list balances")


@router.get("/balances/{entity_tenant_id}", response_model=IntercompanyBalanceDetailResponse)
async def get_balance_with_entity(request: Request, entity_tenant_id: str):
    """Get balance with specific entity."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get balance record
            balance_row = await conn.fetchrow(
                """
                SELECT * FROM intercompany_balances
                WHERE (entity_a_tenant_id = $1 AND entity_b_tenant_id = $2)
                   OR (entity_a_tenant_id = $2 AND entity_b_tenant_id = $1)
                """,
                ctx["tenant_id"], entity_tenant_id
            )

            balance = 0
            last_tx_date = None
            last_rec_date = None

            if balance_row:
                if balance_row["entity_a_tenant_id"] == ctx["tenant_id"]:
                    balance = balance_row["balance"]
                else:
                    balance = -balance_row["balance"]
                last_tx_date = balance_row["last_transaction_date"]
                last_rec_date = balance_row["last_reconciled_date"]

            # Get recent transactions
            tx_rows = await conn.fetch(
                """
                SELECT * FROM intercompany_transactions
                WHERE (from_entity_tenant_id = $1 AND to_entity_tenant_id = $2)
                   OR (from_entity_tenant_id = $2 AND to_entity_tenant_id = $1)
                ORDER BY transaction_date DESC
                LIMIT 20
                """,
                ctx["tenant_id"], entity_tenant_id
            )

            recent_tx = [
                {
                    "id": str(row["id"]),
                    "transaction_number": row["transaction_number"],
                    "transaction_date": row["transaction_date"],
                    "from_entity_tenant_id": row["from_entity_tenant_id"],
                    "from_entity_name": None,
                    "to_entity_tenant_id": row["to_entity_tenant_id"],
                    "to_entity_name": None,
                    "transaction_type": row["transaction_type"],
                    "amount": row["amount"],
                    "currency_code": None,
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "is_reconciled": row["is_reconciled"],
                    "created_at": row["created_at"],
                }
                for row in tx_rows
            ]

            return {
                "success": True,
                "data": {
                    "entity_tenant_id": entity_tenant_id,
                    "entity_name": None,
                    "balance": balance,
                    "currency_code": None,
                    "last_transaction_date": last_tx_date,
                    "last_reconciled_date": last_rec_date,
                    "transaction_count": len(recent_tx),
                },
                "recent_transactions": recent_tx
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting IC balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get balance")


# =============================================================================
# REPORTS
# =============================================================================
@router.get("/report", response_model=IntercompanyReportResponse)
async def get_report(
    request: Request,
    start_date: date = Query(...),
    end_date: date = Query(...),
    entity_tenant_id: Optional[str] = Query(None),
    transaction_type: Optional[str] = Query(None),
):
    """Get IC transaction report."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = [
                "(from_entity_tenant_id = $1 OR to_entity_tenant_id = $1)",
                "transaction_date >= $2",
                "transaction_date <= $3"
            ]
            params = [ctx["tenant_id"], start_date, end_date]
            param_idx = 4

            if entity_tenant_id:
                conditions.append(f"(from_entity_tenant_id = ${param_idx} OR to_entity_tenant_id = ${param_idx})")
                params.append(entity_tenant_id)
                param_idx += 1

            if transaction_type:
                conditions.append(f"transaction_type = ${param_idx}")
                params.append(transaction_type)

            where_clause = " AND ".join(conditions)

            rows = await conn.fetch(
                f"""
                SELECT * FROM intercompany_transactions
                WHERE {where_clause}
                ORDER BY transaction_date
                """,
                *params
            )

            transactions = [
                {
                    "id": str(row["id"]),
                    "transaction_number": row["transaction_number"],
                    "transaction_date": row["transaction_date"],
                    "from_entity_tenant_id": row["from_entity_tenant_id"],
                    "from_entity_name": None,
                    "to_entity_tenant_id": row["to_entity_tenant_id"],
                    "to_entity_name": None,
                    "transaction_type": row["transaction_type"],
                    "amount": row["amount"],
                    "currency_code": None,
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "is_reconciled": row["is_reconciled"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            # Calculate summary
            total_sent = sum(row["amount"] for row in rows if row["from_entity_tenant_id"] == ctx["tenant_id"])
            total_received = sum(row["amount"] for row in rows if row["to_entity_tenant_id"] == ctx["tenant_id"])
            reconciled_count = sum(1 for row in rows if row["is_reconciled"])

            return {
                "success": True,
                "period_start": start_date,
                "period_end": end_date,
                "transactions": transactions,
                "summary": {
                    "total_transactions": len(transactions),
                    "total_sent": total_sent,
                    "total_received": total_received,
                    "reconciled_count": reconciled_count,
                    "unreconciled_count": len(transactions) - reconciled_count
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating IC report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate report")


@router.get("/aging", response_model=IntercompanyAgingResponse)
async def get_aging_report(request: Request):
    """Get IC aging report."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        today = date.today()

        async with pool.acquire() as conn:
            # Get unreconciled transactions grouped by counterparty and age
            rows = await conn.fetch(
                """
                SELECT
                    CASE
                        WHEN from_entity_tenant_id = $1 THEN to_entity_tenant_id
                        ELSE from_entity_tenant_id
                    END as counterparty,
                    SUM(CASE WHEN CURRENT_DATE - transaction_date <= 30 THEN amount ELSE 0 END) as current,
                    SUM(CASE WHEN CURRENT_DATE - transaction_date BETWEEN 31 AND 60 THEN amount ELSE 0 END) as days_31_60,
                    SUM(CASE WHEN CURRENT_DATE - transaction_date BETWEEN 61 AND 90 THEN amount ELSE 0 END) as days_61_90,
                    SUM(CASE WHEN CURRENT_DATE - transaction_date > 90 THEN amount ELSE 0 END) as over_90,
                    SUM(amount) as total
                FROM intercompany_transactions
                WHERE (from_entity_tenant_id = $1 OR to_entity_tenant_id = $1)
                  AND is_reconciled = false
                GROUP BY counterparty
                ORDER BY total DESC
                """,
                ctx["tenant_id"]
            )

            items = [
                {
                    "entity_tenant_id": row["counterparty"],
                    "entity_name": None,
                    "current": row["current"] or 0,
                    "days_31_60": row["days_31_60"] or 0,
                    "days_61_90": row["days_61_90"] or 0,
                    "over_90_days": row["over_90"] or 0,
                    "total": row["total"] or 0,
                }
                for row in rows
            ]

            totals = {
                "current": sum(i["current"] for i in items),
                "days_31_60": sum(i["days_31_60"] for i in items),
                "days_61_90": sum(i["days_61_90"] for i in items),
                "over_90_days": sum(i["over_90_days"] for i in items),
                "total": sum(i["total"] for i in items),
            }

            return {
                "success": True,
                "as_of_date": today,
                "items": items,
                "totals": totals
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating IC aging: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate aging report")
