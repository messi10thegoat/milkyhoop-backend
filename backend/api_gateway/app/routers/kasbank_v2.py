"""
KasBank V2 Router - Cash & Bank Module V2

Unified endpoints for bank account management, manual transactions,
and bank transfers. Uses the DRAFT -> POST -> VOID workflow for
manual transactions and transfers.

Balances are derived from journal_entries/journal_lines (not denormalized).
Bank transaction running_balance is maintained by DB trigger
(trg_update_bank_balance) which atomically updates bank_accounts.current_balance.

Endpoints:
- GET    /bank-accounts                           - List all bank accounts
- GET    /bank-accounts/{id}                      - Get bank account detail
- GET    /bank-accounts/{id}/transactions          - List transactions for account
- POST   /bank-accounts/{id}/transactions          - Create manual transaction (draft)
- POST   /bank-transactions/{id}/post              - Post a draft transaction
- POST   /bank-transactions/{id}/void              - Void a posted transaction
- GET    /bank-transactions/{id}                   - Get transaction detail
- POST   /bank-transfers                           - Create transfer (draft)
- POST   /bank-transfers/{id}/post                 - Post a draft transfer
- POST   /bank-transfers/{id}/void                 - Void a posted transfer
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from pydantic import BaseModel
from uuid import UUID
from datetime import date
import logging
import asyncpg
import uuid as uuid_module

from ..schemas.kasbank_v2 import (
    CreateManualTransactionRequest,
    VoidTransactionRequest,
    CreateTransferRequest,
    VoidTransferRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class PostTransactionRequest(BaseModel):
    recon_session_id: Optional[str] = None
    statement_line_id: Optional[str] = None


# Connection pool


# =============================================================================
# HELPERS
# =============================================================================


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


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    """Check if the accounting period for the transaction date is open."""
    period = await conn.fetchrow(
        """
        SELECT id, period_name, status FROM fiscal_periods
        WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
        ORDER BY start_date DESC LIMIT 1
        """,
        tenant_id,
        transaction_date,
    )
    if period and period["status"] in ("CLOSED", "LOCKED"):
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
        )


async def generate_transaction_number(conn, tenant_id: str) -> str:
    """Generate a unique transaction number: BT-YYMM-NNNN."""
    from datetime import datetime as dt

    now = dt.now()
    prefix = f"BT-{now.strftime('%y%m')}-"

    last = await conn.fetchval(
        """
        SELECT transaction_number FROM bank_transactions
        WHERE tenant_id = $1 AND transaction_number LIKE $2
        ORDER BY transaction_number DESC LIMIT 1
        """,
        tenant_id,
        prefix + "%",
    )

    if last:
        try:
            seq = int(last.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:04d}"


def _serialize_tx(row) -> dict:
    """Serialize a bank_transactions row to dict for response.
    Iron Law 1: Uses journal_amount when available (ledger supremacy).
    """
    # Use journal-derived amount if present, else fall back to bt.amount
    amount = row.get("journal_amount", row["amount"])
    return {
        "id": str(row["id"]),
        "transaction_number": row["transaction_number"],
        "transaction_date": row["transaction_date"].isoformat()
        if row["transaction_date"]
        else None,
        "transaction_type": row["transaction_type"],
        "amount": int(amount),
        "running_balance": int(row["running_balance"]),
        "description": row["description"],
        "reference_type": row.get("reference_type"),
        "reference_id": str(row["reference_id"]) if row.get("reference_id") else None,
        "reference_number": row.get("reference_number"),
        "payee_payer": row.get("payee_payer"),
        "status": row["status"],
        "origin_type": row["origin_type"],
        "source_module": row.get("source_module"),
        "is_reconciled": row.get("is_reconciled", False),
        "reconciliation_status": row.get("reconciliation_status", "UNRECONCILED"),
        "journal_id": str(row["journal_id"]) if row.get("journal_id") else None,
        "posted_at": row["posted_at"].isoformat() if row.get("posted_at") else None,
        "voided_at": row["voided_at"].isoformat() if row.get("voided_at") else None,
        "void_reason": row.get("void_reason"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


# Transaction type -> (bank_tx_type, amount_sign)
# deposit types: money comes IN (positive)
# withdrawal types: money goes OUT (negative)
TX_TYPE_MAP = {
    "other_income": ("deposit", 1),
    "interest_income": ("deposit", 1),
    "owner_contribution": ("deposit", 1),
    "bank_admin_fee": ("withdrawal", -1),
    "owner_drawing": ("withdrawal", -1),
    "card_payment": ("withdrawal", -1),
    "expense": ("withdrawal", -1),
}

# Default contra account codes per transaction type
# These are HINTS resolved at runtime from chart_of_accounts (Law 27)
TRANSACTION_TYPE_CONTRA_DEFAULTS: dict[str, str] = {
    # Law 27: Account codes resolved at runtime via resolve_account_id
    "other_income": "4-90200",  # Pendapatan Lainnya
    "interest_income": "4-90100",  # Pendapatan Bunga
    "owner_contribution": "3-10100",  # Modal Pemilik
    "owner_drawing": "3-40000",  # Prive
    "bank_admin_fee": "5-20800",  # Biaya Admin Bank
    "card_payment": "2-10700",  # Utang Kartu Kredit
}


async def resolve_contra_account(
    conn, tenant_id: str, transaction_type: str, contra_account_id: str | None
) -> str:
    """
    Resolve contra account: explicit payload > type default > error.
    All resolution via chart_of_accounts query (Law 27).
    """
    # 1. Explicit from request payload - validate and return
    if contra_account_id:
        account = await conn.fetchrow(
            "SELECT id FROM chart_of_accounts WHERE id = $1::uuid AND tenant_id = $2 AND is_active = true",
            contra_account_id,
            tenant_id,
        )
        if not account:
            raise HTTPException(400, "Akun lawan tidak ditemukan atau tidak aktif.")
        return contra_account_id

    # 2. Default from transaction type
    default_code = TRANSACTION_TYPE_CONTRA_DEFAULTS.get(transaction_type)
    if default_code:
        account_id = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2 AND is_active = true",
            tenant_id,
            default_code,
        )
        if account_id:
            return str(account_id)
        # Default account not found - informative error
        raise HTTPException(
            400,
            f"Akun default '{default_code}' tidak ditemukan untuk tipe '{transaction_type}'. "
            f"Pastikan akun ini aktif di Daftar Akun.",
        )

    # 3. Types that REQUIRE explicit contra account (expense, other_income)
    raise HTTPException(400, "Akun lawan wajib dipilih untuk tipe transaksi ini.")


# =============================================================================
# HEALTH CHECK
# =============================================================================


@router.get("/kasbank-v2/health")
async def health_check():
    return {"status": "ok", "service": "kasbank-v2", "version": "2.0"}


# =============================================================================
# BANK ACCOUNTS
# =============================================================================


@router.get("/bank-accounts", tags=["kasbank-v2"])
async def list_accounts(request: Request, include_inactive: bool = Query(False)):
    """
    List all bank accounts with journal-derived balances.

    Balance = SUM(debit - credit) from journal_lines for the account's coa_id.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            active_filter = "" if include_inactive else "AND ba.is_active = true"
            rows = await conn.fetch(
                f"""
                SELECT
                    ba.id, ba.account_name, ba.bank_name, ba.account_number,
                    ba.account_type, ba.currency, ba.is_active, ba.is_default,
                    ba.opening_balance, ba.coa_id, ba.created_at, ba.updated_at,
                    COALESCE(bal.journal_balance, 0) as current_balance,
                    COALESCE(txc.tx_count, 0) as transaction_count
                FROM bank_accounts ba
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit - jl.credit), 0) as journal_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON jl.journal_id = je.id
                    WHERE je.tenant_id = ba.tenant_id
                      AND je.status = 'POSTED'
                      AND jl.account_id = ba.coa_id
                ) bal ON true
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) as tx_count
                    FROM bank_transactions bt
                    WHERE bt.bank_account_id = ba.id
                      AND bt.tenant_id = ba.tenant_id
                      AND bt.transaction_type != 'opening'
                ) txc ON true
                WHERE ba.tenant_id = $1 {active_filter}
                ORDER BY ba.account_name
                """,
                ctx["tenant_id"],
            )

            items = [
                {
                    "id": str(row["id"]),
                    "account_name": row["account_name"],
                    "bank_name": row["bank_name"],
                    "account_number": row["account_number"],
                    "account_type": row["account_type"],
                    "currency": (row["currency"] or "IDR").strip(),
                    "is_active": row["is_active"],
                    "is_default": row["is_default"],
                    "opening_balance": int(row["opening_balance"] or 0),
                    "current_balance": int(row["current_balance"]),
                    "transaction_count": int(row["transaction_count"]),
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                    "updated_at": row["updated_at"].isoformat()
                    if row["updated_at"]
                    else None,
                }
                for row in rows
            ]

            return {"success": True, "data": items, "total": len(items)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bank accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bank accounts")


@router.get("/bank-accounts/{account_id}", tags=["kasbank-v2"])
async def get_account_detail(request: Request, account_id: UUID):
    """Get detailed information for a single bank account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    ba.id, ba.account_name, ba.bank_name, ba.account_number,
                    ba.account_type, ba.currency, ba.is_active,
                    ba.opening_balance, ba.coa_id,
                    COALESCE(bal.journal_balance, 0) as current_balance
                FROM bank_accounts ba
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit - jl.credit), 0) as journal_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON jl.journal_id = je.id
                    WHERE je.tenant_id = ba.tenant_id
                      AND je.status = 'POSTED'
                      AND jl.account_id = ba.coa_id
                ) bal ON true
                WHERE ba.id = $1 AND ba.tenant_id = $2
                """,
                account_id,
                ctx["tenant_id"],
            )

            if not row:
                raise HTTPException(status_code=404, detail="Bank account not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "account_name": row["account_name"],
                    "bank_name": row["bank_name"],
                    "account_number": row["account_number"],
                    "account_type": row["account_type"],
                    "currency": (row["currency"] or "IDR").strip(),
                    "is_active": row["is_active"],
                    "current_balance": int(row["current_balance"]),
                    "opening_balance": int(row["opening_balance"] or 0),
                    "coa_id": str(row["coa_id"]),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bank account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bank account")


# =============================================================================
# BANK TRANSACTIONS - LIST & DETAIL
# =============================================================================


@router.get("/bank-accounts/{account_id}/transactions", tags=["kasbank-v2"])
async def list_transactions(
    request: Request,
    account_id: UUID,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    direction: Optional[Literal["masuk", "keluar"]] = Query(
        None, description="Filter by direction: masuk (in) / keluar (out)"
    ),
    source: Optional[
        Literal["manual", "expense", "invoice_payment", "bill_payment", "transfer"]
    ] = Query(None, description="Filter by source module"),
    status: Optional[Literal["DRAFT", "POSTED", "VOIDED"]] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """
    List bank transactions for a specific account.
    Supports filtering by date range, direction (masuk/keluar), source module, and status.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Validate account exists and belongs to tenant
            acct = await conn.fetchrow(
                "SELECT id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                account_id,
                ctx["tenant_id"],
            )
            if not acct:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Build conditions
            conditions = ["bt.bank_account_id = $1", "bt.tenant_id = $2"]
            params: list = [account_id, ctx["tenant_id"]]
            param_idx = 3

            # Default: exclude voided unless explicitly requested
            if status:
                conditions.append(f"bt.status = ${param_idx}")
                params.append(status)
                param_idx += 1
            else:
                conditions.append("bt.status != 'VOIDED'")

            if date_from:
                conditions.append(f"bt.transaction_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"bt.transaction_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            if direction == "masuk":
                conditions.append("bt.amount > 0")
            elif direction == "keluar":
                conditions.append("bt.amount < 0")

            if source:
                if source == "manual":
                    conditions.append("bt.origin_type = 'MANUAL'")
                else:
                    conditions.append(f"bt.source_module = ${param_idx}")
                    params.append(source)
                    param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count total
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bank_transactions bt WHERE {where_clause}",
                *params,
            )

            # Fetch items
            offset = (page - 1) * per_page
            params.extend([per_page, offset])

            # Iron Law 1: JOIN to journal_lines for ledger-derived amounts
            # Fallback to bt.amount when journal_id IS NULL (unreconciled imports)
            acct_coa = await conn.fetchval(
                "SELECT coa_id FROM bank_accounts WHERE id = $1",
                account_id,
            )
            rows = await conn.fetch(
                f"""
                SELECT bt.*,
                    CASE
                        WHEN bt.journal_id IS NOT NULL AND jl.account_id IS NOT NULL
                        THEN COALESCE(jl.debit, 0) - COALESCE(jl.credit, 0)
                        ELSE bt.amount
                    END as journal_amount
                FROM bank_transactions bt
                LEFT JOIN journal_lines jl
                    ON jl.journal_id = bt.journal_id AND jl.account_id = ${param_idx + 2}
                WHERE {where_clause}
                ORDER BY bt.transaction_date DESC, bt.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params,
                acct_coa,
            )

            items = [_serialize_tx(row) for row in rows]
            total_pages = (total + per_page - 1) // per_page if total else 0

            return {
                "success": True,
                "data": {
                    "items": items,
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": total_pages,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing transactions for {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list transactions")


@router.get("/bank-transactions/{transaction_id}", tags=["kasbank-v2"])
async def get_transaction_detail(request: Request, transaction_id: UUID):
    """Get detailed information for a single bank transaction."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM bank_transactions WHERE id = $1 AND tenant_id = $2",
                transaction_id,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(
                    status_code=404, detail="Bank transaction not found"
                )

            return {"success": True, "data": _serialize_tx(row)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting transaction {transaction_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transaction")


# =============================================================================
# MANUAL TRANSACTIONS - CREATE / POST / VOID
# =============================================================================


@router.post("/bank-accounts/{account_id}/transactions", tags=["kasbank-v2"])
async def create_manual_transaction(
    request: Request,
    account_id: UUID,
    body: CreateManualTransactionRequest,
):
    """
    Create a manual bank transaction in DRAFT status.

    The frontend maps each action type (e.g. 'Pendapatan Lain', 'Biaya Admin Bank')
    to a transaction_type and provides the contra CoA account.

    Amount is always positive in the request. The sign is determined by transaction_type.

    NOTE: The DB has a trigger (trg_update_bank_balance) that fires on every INSERT
    to bank_transactions and atomically updates bank_accounts.current_balance.
    For DRAFT transactions, we immediately reverse this balance change so the
    balance is only affected when the transaction is POSTed.

    The contra_account_id is stored in reference_id (UUID field) with
    reference_type = 'manual_contra' so POST can retrieve it.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Validate bank account
                bank = await conn.fetchrow(
                    """
                    SELECT id, account_name, coa_id, is_active
                    FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    account_id,
                    ctx["tenant_id"],
                )
                if not bank:
                    raise HTTPException(
                        status_code=404, detail="Bank account not found"
                    )
                if not bank["is_active"]:
                    raise HTTPException(
                        status_code=400, detail="Bank account is inactive"
                    )

                # Resolve contra account (auto-resolve for fixed types, Law 27)
                resolved_contra_id = await resolve_contra_account(
                    conn,
                    ctx["tenant_id"],
                    body.transaction_type,
                    body.contra_account_id,
                )

                # Map transaction type
                tx_type, sign = TX_TYPE_MAP[body.transaction_type]
                signed_amount = body.amount * sign

                # Generate transaction number
                tx_number = await generate_transaction_number(conn, ctx["tenant_id"])

                # Create bank transaction in DRAFT status
                # Store contra_account_id in reference_id for POST to use
                tx_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date,
                        transaction_type, amount, running_balance,
                        reference_type, reference_id,
                        description, payee_payer,
                        status, origin_type, source_module, transaction_number,
                        created_by
                    ) VALUES (
                        $1, $2, $3, $4,
                        $5, $6, 0,
                        'manual_contra', $7,
                        $8, $9,
                        'DRAFT', 'MANUAL', $10, $11,
                        $12
                    )
                    """,
                    tx_id,
                    ctx["tenant_id"],
                    account_id,
                    body.transaction_date,
                    tx_type,
                    signed_amount,
                    UUID(resolved_contra_id),
                    body.description,
                    body.contact_name,
                    body.transaction_type,  # source_module stores the specific sub-type
                    tx_number,
                    ctx["user_id"],
                )

                if not body.auto_post:
                    # Law 21: current_balance cache deprecated (v3.5). Balance derived from journal.
                    # await conn.execute(
                    #     """
                    #     UPDATE bank_accounts
                    #     SET current_balance = current_balance - $2, updated_at = NOW()
                    #     WHERE id = $1
                    #     """,
                    #     account_id,
                    #     signed_amount,
                    # )
                    pass

                    row = await conn.fetchrow(
                        "SELECT * FROM bank_transactions WHERE id = $1", tx_id
                    )
                    return {
                        "success": True,
                        "message": "Manual transaction created as DRAFT",
                        "data": _serialize_tx(row),
                    }

                # ============================================================
                # AUTO_POST mode: atomic create + journal + recon in 1 txn
                # Per Bank Sync Rule 1: journal + bank_txn must be atomic
                # ============================================================

                # Advisory lock (Rule 4)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TX:{tx_id}:{account_id}",
                )

                # Check period is open
                await check_period_is_open(
                    conn, ctx["tenant_id"], body.transaction_date
                )

                # Contra already resolved above via resolve_contra_account
                # No need to re-validate here

                abs_amount = abs(signed_amount)
                bank_coa_id = bank["coa_id"]
                is_deposit = signed_amount > 0

                # Create journal entry
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = f"JR-{tx_number}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'BANK_TRANSACTION', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.transaction_date,
                    body.description or f"Manual transaction {tx_number}",
                    tx_id,
                    str(trace_id),
                    abs_amount,
                    ctx["user_id"],
                )

                if is_deposit:
                    # Dr Bank, Cr Contra
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        bank_coa_id,
                        abs_amount,
                        f"Deposit - {body.description or tx_number}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        UUID(resolved_contra_id),
                        abs_amount,
                        f"Deposit - {body.description or tx_number}",
                    )
                else:
                    # Dr Contra, Cr Bank (withdrawal)
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        UUID(resolved_contra_id),
                        abs_amount,
                        f"Withdrawal - {body.description or tx_number}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        bank_coa_id,
                        abs_amount,
                        f"Withdrawal - {body.description or tx_number}",
                    )

                # Update bank_transaction: POSTED + journal link
                # Balance already correct (trigger applied, no reversal needed)

                # Post the journal (triggers hash chain: Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )
                # Law 21: running_balance from journal, not current_balance cache
                new_balance_row = await conn.fetchrow(
                    """SELECT (SELECT COALESCE(SUM(jl.debit)-SUM(jl.credit),0)
                     FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                     WHERE jl.account_id=ba.coa_id AND je.status='POSTED') as balance
                    FROM bank_accounts ba WHERE ba.id = $1""",
                    account_id,
                )
                new_balance = int(new_balance_row["balance"]) if new_balance_row else 0

                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET status = 'POSTED',
                        journal_id = $2,
                        running_balance = $3,
                        posted_by = $4,
                        posted_at = NOW()
                    WHERE id = $1
                    """,
                    tx_id,
                    journal_id,
                    new_balance,
                    ctx["user_id"],
                )

                # Auto-match with reconciliation if context provided
                recon_matched = False
                if body.recon_session_id and body.statement_line_id:
                    try:
                        recon_session_id = UUID(body.recon_session_id)
                        recon_line_id = UUID(body.statement_line_id)

                        recon_session = await conn.fetchrow(
                            "SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                            recon_session_id,
                            ctx["tenant_id"],
                        )
                        if recon_session and recon_session["status"] == "in_progress":
                            stmt_line = await conn.fetchrow(
                                "SELECT id, match_status FROM bank_statement_lines_v2 WHERE id = $1 AND session_id = $2",
                                recon_line_id,
                                recon_session_id,
                            )
                            if stmt_line and stmt_line["match_status"] != "matched":
                                match_id = uuid_module.uuid4()
                                await conn.execute(
                                    """
                                    INSERT INTO reconciliation_matches (
                                        id, tenant_id, session_id, statement_line_id, transaction_id,
                                        match_type, confidence, created_by, created_at
                                    ) VALUES ($1, $2, $3, $4, $5, 'one_to_one', 'manual', $6, NOW())
                                    """,
                                    match_id,
                                    ctx["tenant_id"],
                                    recon_session_id,
                                    recon_line_id,
                                    tx_id,
                                    ctx["user_id"],
                                )
                                await conn.execute(
                                    """
                                    UPDATE bank_transactions
                                    SET is_cleared = true, cleared_at = NOW(), matched_statement_line_id = $2
                                    WHERE id = $1
                                    """,
                                    tx_id,
                                    recon_line_id,
                                )
                                await conn.execute(
                                    """
                                    UPDATE bank_statement_lines_v2
                                    SET match_status = 'matched', match_confidence = 'manual'
                                    WHERE id = $1
                                    """,
                                    recon_line_id,
                                )

                                from .bank_reconciliation import (
                                    update_reconciliation_session_stats,
                                )

                                await update_reconciliation_session_stats(
                                    conn, recon_session_id
                                )

                                recon_matched = True
                                logger.info(
                                    f"Auto-matched tx {tx_id} with statement line {recon_line_id}"
                                )
                    except Exception as recon_err:
                        logger.warning(
                            f"Auto-match failed (within atomic txn): {recon_err}"
                        )
                        # Don't fail the whole transaction for recon match failure

                row = await conn.fetchrow(
                    "SELECT * FROM bank_transactions WHERE id = $1", tx_id
                )

                logger.info(f"Auto-posted manual tx: {tx_id}, journal={journal_id}")

                return {
                    "success": True,
                    "message": "Transaction created and posted",
                    "data": _serialize_tx(row),
                    "recon_matched": recon_matched,
                }

    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e)
        logger.error(f"Error creating manual transaction: {err_str}", exc_info=True)
        # Detect header account trigger error and give user-friendly message
        if "header/parent account" in err_str:
            raise HTTPException(
                status_code=400,
                detail="Akun yang dipilih adalah akun induk (header). Pilih akun spesifik (anak) untuk mencatat transaksi.",
            )
        raise HTTPException(status_code=500, detail="Failed to create transaction")


@router.post("/bank-transactions/{transaction_id}/post", tags=["kasbank-v2"])
async def post_transaction(
    request: Request, transaction_id: UUID, body: PostTransactionRequest = None
):
    """
    Post a DRAFT manual bank transaction.

    Creates journal entry and updates the bank balance.
    The journal has 2 lines:
    - For deposits: Dr Bank, Cr Contra
    - For withdrawals: Dr Contra, Cr Bank (amount is negative in bank_tx)
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: advisory lock prevents concurrent duplicate
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TX:{transaction_id}",
                )
                # Lock the transaction row
                tx = await conn.fetchrow(
                    """
                    SELECT bt.*, ba.coa_id as bank_coa_id, ba.account_name as bank_name
                    FROM bank_transactions bt
                    JOIN bank_accounts ba ON bt.bank_account_id = ba.id
                    WHERE bt.id = $1 AND bt.tenant_id = $2
                    FOR UPDATE OF bt
                    """,
                    transaction_id,
                    ctx["tenant_id"],
                )
                if not tx:
                    raise HTTPException(status_code=404, detail="Transaction not found")

                if tx["status"] != "DRAFT":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post transaction with status '{tx['status']}'",
                    )

                if tx["origin_type"] != "MANUAL":
                    raise HTTPException(
                        status_code=400,
                        detail="Only manual transactions can be posted from this endpoint",
                    )

                # Check period is open
                await check_period_is_open(
                    conn, ctx["tenant_id"], tx["transaction_date"]
                )

                # Retrieve contra_account_id from reference_id
                # (stored during create with reference_type='manual_contra')
                contra_account_id = tx["reference_id"]
                if not contra_account_id:
                    raise HTTPException(
                        status_code=400,
                        detail="Transaction missing contra account. Please void and recreate.",
                    )

                # Validate contra account still exists
                contra = await conn.fetchrow(
                    "SELECT id, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                    contra_account_id,
                    ctx["tenant_id"],
                )
                if not contra:
                    raise HTTPException(
                        status_code=400, detail="Contra account no longer exists"
                    )

                abs_amount = abs(int(tx["amount"]))
                bank_coa_id = tx["bank_coa_id"]
                is_deposit = int(tx["amount"]) > 0

                # Create journal entry
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = f"JR-{tx['transaction_number']}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'BANK_TRANSACTION', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    tx["transaction_date"],
                    tx["description"]
                    or f"Manual transaction {tx['transaction_number']}",
                    transaction_id,
                    str(trace_id),
                    abs_amount,
                    ctx["user_id"],
                )

                if is_deposit:
                    # Dr Bank, Cr Contra
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        bank_coa_id,
                        abs_amount,
                        f"Deposit - {tx['description'] or tx['transaction_number']}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        contra_account_id,
                        abs_amount,
                        f"Deposit - {tx['description'] or tx['transaction_number']}",
                    )
                else:
                    # Dr Contra, Cr Bank (withdrawal)
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        contra_account_id,
                        abs_amount,
                        f"Withdrawal - {tx['description'] or tx['transaction_number']}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        bank_coa_id,
                        abs_amount,
                        f"Withdrawal - {tx['description'] or tx['transaction_number']}",
                    )

                # Now re-apply the balance (the DRAFT create had reversed it)
                # Update bank_accounts.current_balance

                # Post the journal (triggers hash chain: Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )
                signed_amount = int(tx["amount"])
                # Law 21: current_balance cache deprecated (v3.5). Balance derived from journal.
                # await conn.execute(
                #     """
                #     UPDATE bank_accounts
                #     SET current_balance = current_balance + $2, updated_at = NOW()
                #     WHERE id = $1
                #     """,
                #     tx["bank_account_id"],
                #     signed_amount,
                # )
                new_balance_row = await conn.fetchrow(
                    """SELECT (SELECT COALESCE(SUM(jl.debit)-SUM(jl.credit),0)
                     FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                     WHERE jl.account_id=ba.coa_id AND je.status='POSTED') as balance
                    FROM bank_accounts ba WHERE ba.id = $1""",
                    tx["bank_account_id"],
                )
                new_balance = int(new_balance_row["balance"]) if new_balance_row else 0

                # Update transaction: status -> POSTED, set journal_id, running_balance
                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET status = 'POSTED',
                        journal_id = $2,
                        running_balance = $3,
                        posted_by = $4,
                        posted_at = NOW()
                    WHERE id = $1
                    """,
                    transaction_id,
                    journal_id,
                    new_balance,
                    ctx["user_id"],
                )

                # Fetch updated record
                row = await conn.fetchrow(
                    "SELECT * FROM bank_transactions WHERE id = $1", transaction_id
                )

                logger.info(
                    f"Manual transaction posted: {transaction_id}, journal={journal_id}"
                )

                posted_data = _serialize_tx(row)

            # === Optional: Auto-match with reconciliation statement line ===
            # This runs OUTSIDE the main transaction block so posting is safe even if recon fails
            recon_matched = False
            if body and body.recon_session_id and body.statement_line_id:
                try:
                    async with conn.transaction():
                        recon_session_id = UUID(body.recon_session_id)
                        recon_line_id = UUID(body.statement_line_id)

                        # Verify session is in_progress
                        recon_session = await conn.fetchrow(
                            "SELECT status, account_id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                            recon_session_id,
                            ctx["tenant_id"],
                        )
                        if recon_session and recon_session["status"] == "in_progress":
                            # Verify statement line is unmatched
                            stmt_line = await conn.fetchrow(
                                "SELECT id, match_status FROM bank_statement_lines_v2 WHERE id = $1 AND session_id = $2",
                                recon_line_id,
                                recon_session_id,
                            )
                            if stmt_line and stmt_line["match_status"] != "matched":
                                import uuid as uuid_std

                                # 1. Insert reconciliation_matches
                                match_id = uuid_std.uuid4()
                                await conn.execute(
                                    """
                                    INSERT INTO reconciliation_matches (
                                        id, tenant_id, session_id, statement_line_id, transaction_id,
                                        match_type, confidence, created_by, created_at
                                    ) VALUES ($1, $2, $3, $4, $5, 'one_to_one', 'manual', $6, NOW())
                                    """,
                                    match_id,
                                    ctx["tenant_id"],
                                    recon_session_id,
                                    recon_line_id,
                                    transaction_id,
                                    ctx["user_id"],
                                )

                                # 2. Update bank_transactions: is_cleared
                                await conn.execute(
                                    """
                                    UPDATE bank_transactions
                                    SET is_cleared = true, cleared_at = NOW(), matched_statement_line_id = $2
                                    WHERE id = $1
                                    """,
                                    transaction_id,
                                    recon_line_id,
                                )

                                # 3. Update statement line: match_status
                                await conn.execute(
                                    """
                                    UPDATE bank_statement_lines_v2
                                    SET match_status = 'matched', match_confidence = 'manual'
                                    WHERE id = $1
                                    """,
                                    recon_line_id,
                                )

                                # 4. Update session stats
                                from .bank_reconciliation import (
                                    update_reconciliation_session_stats,
                                )

                                await update_reconciliation_session_stats(
                                    conn, recon_session_id
                                )

                                # 5. Record match history (non-critical)
                                try:
                                    from .bank_reconciliation import (
                                        record_match_history,
                                    )

                                    await record_match_history(
                                        conn=conn,
                                        tenant_id=ctx["tenant_id"],
                                        session_id=recon_session_id,
                                        statement_line_id=recon_line_id,
                                        transaction_id=transaction_id,
                                        match_confidence="manual",
                                        match_method="auto_on_create",
                                        user_id=ctx["user_id"],
                                    )
                                except Exception as hist_err:
                                    logger.warning(
                                        f"Failed to record match history: {hist_err}"
                                    )

                                recon_matched = True
                                logger.info(
                                    f"Auto-matched transaction {transaction_id} with statement line {recon_line_id}"
                                )

                except Exception as recon_err:
                    logger.warning(f"Failed to auto-match recon: {recon_err}")
                    # Don't fail the post - transaction is already committed

            return {
                "success": True,
                "message": "Transaction posted to accounting",
                "data": posted_data,
                "recon_matched": recon_matched,
            }

    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e)
        logger.error(
            f"Error posting transaction {transaction_id}: {err_str}", exc_info=True
        )
        if "header/parent account" in err_str:
            raise HTTPException(
                status_code=400,
                detail="Akun yang dipilih adalah akun induk (header). Pilih akun spesifik (anak) untuk mencatat transaksi.",
            )
        raise HTTPException(status_code=500, detail="Failed to post transaction")


@router.post("/bank-transactions/{transaction_id}/void", tags=["kasbank-v2"])
async def void_transaction(
    request: Request,
    transaction_id: UUID,
    body: VoidTransactionRequest,
):
    """
    Void a posted manual bank transaction.

    Creates a reversal journal entry (swap debit/credit) and marks
    the original journal as VOID.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: advisory lock prevents concurrent duplicate
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TX_VOID:{transaction_id}",
                )
                # Lock the transaction
                tx = await conn.fetchrow(
                    """
                    SELECT bt.*, ba.coa_id as bank_coa_id
                    FROM bank_transactions bt
                    JOIN bank_accounts ba ON bt.bank_account_id = ba.id
                    WHERE bt.id = $1 AND bt.tenant_id = $2
                    FOR UPDATE OF bt
                    """,
                    transaction_id,
                    ctx["tenant_id"],
                )
                if not tx:
                    raise HTTPException(status_code=404, detail="Transaction not found")

                if tx["status"] == "VOIDED":
                    raise HTTPException(
                        status_code=400, detail="Transaction already voided"
                    )

                # Guard: Opening balance transactions cannot be voided
                if tx["transaction_type"] == "opening":
                    raise HTTPException(
                        status_code=400,
                        detail="Saldo awal tidak bisa di-void. Gunakan Edit Akun untuk mengubah saldo awal.",
                    )

                if tx["status"] == "DRAFT":
                    # Just delete the draft (no journal to reverse, balance wasn't affected)
                    # But we need to reverse the trigger's balance impact first
                    # Actually during create we already reversed it. So just delete.
                    await conn.execute(
                        "DELETE FROM bank_transactions WHERE id = $1",
                        transaction_id,
                    )
                    return {
                        "success": True,
                        "message": "Draft transaction deleted",
                        "data": {"id": str(transaction_id), "status": "DELETED"},
                    }

                # POSTED transaction - need reversal journal
                if tx["reconciliation_status"] == "RECONCILED":
                    raise HTTPException(
                        status_code=400, detail="Cannot void a reconciled transaction"
                    )

                # Check period
                await check_period_is_open(
                    conn, ctx["tenant_id"], tx["transaction_date"]
                )

                # Get original journal lines
                original_lines = await conn.fetch(
                    "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                    tx["journal_id"],
                )

                if not original_lines:
                    raise HTTPException(
                        status_code=500, detail="Original journal lines not found"
                    )

                abs_amount = sum(line["debit"] or 0 for line in original_lines)

                # Create reversal journal
                reversal_journal_id = uuid_module.uuid4()
                reversal_number = f"RV-{tx['transaction_number']}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, reversal_of_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'BANK_TRANSACTION', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                    reversal_journal_id,
                    ctx["tenant_id"],
                    reversal_number,
                    f"Void {tx['transaction_number']} - {body.reason}",
                    transaction_id,
                    tx["journal_id"],
                    abs_amount,
                    ctx["user_id"],
                )

                # Swap debit/credit for each line
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid_module.uuid4(),
                        reversal_journal_id,
                        idx,
                        line["account_id"],
                        (line["credit"] or 0),  # Swap
                        (line["debit"] or 0),  # Swap
                        f"Reversal - {line['memo'] or ''}",
                    )

                # Link original journal to its reversal (keep POSTED so journal-derived
                # balance nets to zero: original + reversal = 0)

                # Post the journal (triggers hash chain: Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_journal_id,
                )
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $2
                    WHERE id = $1
                    """,
                    tx["journal_id"],
                    reversal_journal_id,
                )

                # BankSync Rule 3: Create mirror bank_transaction for void
                # (trigger trg_update_bank_balance handles current_balance automatically)
                mirror_type = (
                    "withdrawal" if tx["transaction_type"] == "deposit" else "deposit"
                )
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, journal_id, status, origin_type, source_module,
                        created_by, posted_by, posted_at
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4,
                              $5, 0, 'manual_void', $6, $7, $8, $9,
                              'POSTED', 'SYSTEM', 'manual', $10, $10, NOW())
                    """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    tx["bank_account_id"],
                    mirror_type,
                    -(
                        tx["amount"]
                    ),  # Negate: deposit becomes negative, withdrawal becomes positive
                    transaction_id,
                    f"VOID-{tx['transaction_number']}",
                    f"Void - {body.reason}",
                    reversal_journal_id,
                    ctx["user_id"],
                )

                # Mark transaction as VOIDED
                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET status = 'VOIDED',
                        voided_by = $2,
                        voided_at = NOW(),
                        void_reason = $3
                    WHERE id = $1
                    """,
                    transaction_id,
                    ctx["user_id"],
                    body.reason,
                )

                row = await conn.fetchrow(
                    "SELECT * FROM bank_transactions WHERE id = $1", transaction_id
                )

                logger.info(
                    f"Transaction voided: {transaction_id}, reversal={reversal_journal_id}"
                )

                return {
                    "success": True,
                    "message": "Transaction voided successfully",
                    "data": _serialize_tx(row),
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding transaction {transaction_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void transaction")


# =============================================================================
# BANK TRANSFERS - CREATE / POST / VOID
# =============================================================================


@router.post("/bank-transfers", tags=["kasbank-v2"])
async def create_transfer(request: Request, body: CreateTransferRequest):
    """
    Create a bank transfer in DRAFT status.

    Both source and destination bank accounts must exist and be active.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Validate from bank
                from_bank = await conn.fetchrow(
                    """
                    SELECT id, account_name, coa_id, is_active
                    FROM bank_accounts
                    WHERE id = $1::uuid AND tenant_id = $2
                    """,
                    body.from_bank_account_id,
                    ctx["tenant_id"],
                )
                if not from_bank:
                    raise HTTPException(
                        status_code=400, detail="Source bank account not found"
                    )
                if not from_bank["is_active"]:
                    raise HTTPException(
                        status_code=400, detail="Source bank account is inactive"
                    )

                # Validate to bank
                to_bank = await conn.fetchrow(
                    """
                    SELECT id, account_name, coa_id, is_active
                    FROM bank_accounts
                    WHERE id = $1::uuid AND tenant_id = $2
                    """,
                    body.to_bank_account_id,
                    ctx["tenant_id"],
                )
                if not to_bank:
                    raise HTTPException(
                        status_code=400, detail="Destination bank account not found"
                    )
                if not to_bank["is_active"]:
                    raise HTTPException(
                        status_code=400, detail="Destination bank account is inactive"
                    )

                # Validate fee account if provided
                if body.fee_account_id:
                    fee_acct = await conn.fetchrow(
                        "SELECT id FROM chart_of_accounts WHERE id = $1::uuid AND tenant_id = $2",
                        body.fee_account_id,
                        ctx["tenant_id"],
                    )
                    if not fee_acct:
                        raise HTTPException(
                            status_code=400, detail="Fee account not found"
                        )

                fee_amount = body.fee_amount or 0
                total_amount = body.amount + fee_amount

                # Generate transfer number using existing DB function
                transfer_number = await conn.fetchval(
                    "SELECT generate_bank_transfer_number($1, 'TRF')",
                    ctx["tenant_id"],
                )

                transfer_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO bank_transfers (
                        id, tenant_id, transfer_number, from_bank_id, to_bank_id,
                        amount, fee_amount, total_amount, fee_account_id,
                        transfer_date, notes, status, created_by
                    ) VALUES ($1, $2, $3, $4::uuid, $5::uuid, $6, $7, $8, $9, $10, $11, 'draft', $12)
                    """,
                    transfer_id,
                    ctx["tenant_id"],
                    transfer_number,
                    body.from_bank_account_id,
                    body.to_bank_account_id,
                    body.amount,
                    fee_amount,
                    total_amount,
                    UUID(body.fee_account_id) if body.fee_account_id else None,
                    body.transfer_date,
                    body.description,
                    ctx["user_id"],
                )

                return {
                    "success": True,
                    "message": "Transfer created as draft",
                    "data": {
                        "id": str(transfer_id),
                        "transfer_number": transfer_number,
                        "status": "draft",
                        "amount": body.amount,
                        "fee_amount": fee_amount,
                        "total_amount": total_amount,
                        "transfer_date": body.transfer_date.isoformat(),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create transfer")


@router.post("/bank-transfers/{transfer_id}/post", tags=["kasbank-v2"])
async def post_transfer(request: Request, transfer_id: UUID):
    """
    Post a draft bank transfer to accounting.

    Creates journal entry:
    - Dr: Destination Bank CoA (amount)
    - Dr: Fee Account (fee_amount, if > 0)
    - Cr: Source Bank CoA (total_amount = amount + fee)

    Also creates 2 bank_transactions (transfer_out + transfer_in)
    and updates bank balances via trigger.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: advisory lock prevents concurrent duplicate
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TRANSFER:{transfer_id}",
                )
                # Lock source bank account to prevent concurrent balance changes
                bt = await conn.fetchrow(
                    """
                    SELECT bt.*,
                           fb.account_name as from_name, fb.coa_id as from_coa_id,
                           tb.account_name as to_name, tb.coa_id as to_coa_id
                    FROM bank_transfers bt
                    JOIN bank_accounts fb ON bt.from_bank_id = fb.id
                    JOIN bank_accounts tb ON bt.to_bank_id = tb.id
                    WHERE bt.id = $1 AND bt.tenant_id = $2
                    FOR UPDATE OF bt
                    """,
                    transfer_id,
                    ctx["tenant_id"],
                )
                if not bt:
                    raise HTTPException(
                        status_code=404, detail="Bank transfer not found"
                    )

                if bt["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post transfer with status '{bt['status']}'",
                    )

                # Check period
                await check_period_is_open(conn, ctx["tenant_id"], bt["transfer_date"])

                amount = int(bt["amount"])
                fee_amount = int(bt["fee_amount"] or 0)
                total_amount = int(bt["total_amount"])

                # Create journal entry
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = f"TRF-{bt['transfer_number']}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'BANK_TRANSFER', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    bt["transfer_date"],
                    f"Transfer {bt['transfer_number']} - {bt['from_name']} ke {bt['to_name']}",
                    transfer_id,
                    str(trace_id),
                    total_amount,
                    ctx["user_id"],
                )

                line_number = 1

                # Dr: Destination Bank
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    bt["to_coa_id"],
                    amount,
                    f"Transfer masuk dari {bt['from_name']}",
                )
                line_number += 1

                # Dr: Fee Account (if applicable)
                if fee_amount > 0 and bt["fee_account_id"]:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        line_number,
                        bt["fee_account_id"],
                        fee_amount,
                        f"Biaya transfer - {bt['transfer_number']}",
                    )
                    line_number += 1

                # Cr: Source Bank
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    bt["from_coa_id"],
                    total_amount,
                    f"Transfer keluar ke {bt['to_name']}",
                )

                # Create bank transactions (trigger handles balance)

                # Post the journal (triggers hash chain: Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )
                from_tx_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, payee_payer, journal_id, status, origin_type, source_module,
                        created_by, posted_by, posted_at
                    ) VALUES ($1, $2, $3, $4, 'transfer_out', $5, 0, 'transfer', $6, $7, $8, $9, $10,
                              'POSTED', 'SYSTEM', 'transfer', $11, $11, NOW())
                    """,
                    from_tx_id,
                    ctx["tenant_id"],
                    bt["from_bank_id"],
                    bt["transfer_date"],
                    -total_amount,  # Negative for outgoing
                    transfer_id,
                    bt["transfer_number"],
                    f"Transfer ke {bt['to_name']}",
                    bt["to_name"],
                    journal_id,
                    ctx["user_id"],
                )

                to_tx_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, payee_payer, journal_id, status, origin_type, source_module,
                        created_by, posted_by, posted_at
                    ) VALUES ($1, $2, $3, $4, 'transfer_in', $5, 0, 'transfer', $6, $7, $8, $9, $10,
                              'POSTED', 'SYSTEM', 'transfer', $11, $11, NOW())
                    """,
                    to_tx_id,
                    ctx["tenant_id"],
                    bt["to_bank_id"],
                    bt["transfer_date"],
                    amount,  # Positive for incoming (no fee)
                    transfer_id,
                    bt["transfer_number"],
                    f"Transfer dari {bt['from_name']}",
                    bt["from_name"],
                    journal_id,
                    ctx["user_id"],
                )

                # Update transfer status
                await conn.execute(
                    """
                    UPDATE bank_transfers
                    SET status = 'posted', journal_id = $2, fee_account_id = $3,
                        from_transaction_id = $4, to_transaction_id = $5,
                        posted_at = NOW(), posted_by = $6, updated_at = NOW()
                    WHERE id = $1
                    """,
                    transfer_id,
                    journal_id,
                    bt["fee_account_id"],
                    from_tx_id,
                    to_tx_id,
                    ctx["user_id"],
                )

                logger.info(f"Transfer posted: {transfer_id}, journal={journal_id}")

                return {
                    "success": True,
                    "message": "Bank transfer posted to accounting",
                    "data": {
                        "id": str(transfer_id),
                        "status": "posted",
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "from_transaction_id": str(from_tx_id),
                        "to_transaction_id": str(to_tx_id),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e)
        logger.error(f"Error posting transfer {transfer_id}: {err_str}", exc_info=True)
        if "header/parent account" in err_str:
            raise HTTPException(
                status_code=400,
                detail="Akun yang dipilih adalah akun induk (header). Pilih akun spesifik (anak) untuk mencatat transaksi.",
            )
        raise HTTPException(status_code=500, detail="Failed to post transfer")


@router.post("/bank-transfers/{transfer_id}/void", tags=["kasbank-v2"])
async def void_transfer(
    request: Request,
    transfer_id: UUID,
    body: VoidTransferRequest,
):
    """
    Void a posted bank transfer.

    Creates reversal journal entry (swap debit/credit), reversal bank transactions,
    and marks both original journal and transfer as voided.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: advisory lock prevents concurrent duplicate
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TRANSFER_VOID:{transfer_id}",
                )
                # Get transfer with bank info
                bt = await conn.fetchrow(
                    """
                    SELECT bt.*,
                           fb.account_name as from_name, fb.coa_id as from_coa_id,
                           tb.account_name as to_name, tb.coa_id as to_coa_id
                    FROM bank_transfers bt
                    JOIN bank_accounts fb ON bt.from_bank_id = fb.id
                    JOIN bank_accounts tb ON bt.to_bank_id = tb.id
                    WHERE bt.id = $1 AND bt.tenant_id = $2
                    FOR UPDATE OF bt
                    """,
                    transfer_id,
                    ctx["tenant_id"],
                )
                if not bt:
                    raise HTTPException(
                        status_code=404, detail="Bank transfer not found"
                    )

                if bt["status"] == "void":
                    raise HTTPException(
                        status_code=400, detail="Transfer already voided"
                    )

                if bt["status"] == "draft":
                    # Delete draft - no accounting to reverse
                    await conn.execute(
                        "DELETE FROM bank_transfers WHERE id = $1", transfer_id
                    )
                    return {
                        "success": True,
                        "message": "Draft transfer deleted",
                        "data": {"id": str(transfer_id), "status": "deleted"},
                    }

                # Check bank transactions are not reconciled
                if bt["from_transaction_id"]:
                    from_tx = await conn.fetchrow(
                        "SELECT reconciliation_status FROM bank_transactions WHERE id = $1",
                        bt["from_transaction_id"],
                    )
                    if from_tx and from_tx["reconciliation_status"] == "RECONCILED":
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot void: source transaction is reconciled",
                        )

                if bt["to_transaction_id"]:
                    to_tx = await conn.fetchrow(
                        "SELECT reconciliation_status FROM bank_transactions WHERE id = $1",
                        bt["to_transaction_id"],
                    )
                    if to_tx and to_tx["reconciliation_status"] == "RECONCILED":
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot void: destination transaction is reconciled",
                        )

                # Check period
                await check_period_is_open(conn, ctx["tenant_id"], bt["transfer_date"])

                total_amount = int(bt["total_amount"])
                amount = int(bt["amount"])

                # Get original journal lines
                original_lines = await conn.fetch(
                    "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                    bt["journal_id"],
                )

                # Create reversal journal
                reversal_journal_id = uuid_module.uuid4()
                reversal_number = f"RV-{bt['transfer_number']}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, reversal_of_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'BANK_TRANSFER', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                    reversal_journal_id,
                    ctx["tenant_id"],
                    reversal_number,
                    f"Void Transfer {bt['transfer_number']} - {body.reason}",
                    transfer_id,
                    bt["journal_id"],
                    total_amount,
                    ctx["user_id"],
                )

                # Reversed lines (swap debit/credit)
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid_module.uuid4(),
                        reversal_journal_id,
                        idx,
                        line["account_id"],
                        (line["credit"] or 0),
                        (line["debit"] or 0),
                        f"Reversal - {line['memo'] or ''}",
                    )

                # Link original journal to its reversal (keep POSTED so journal-derived
                # balance nets to zero: original + reversal = 0)

                # Post the journal (triggers hash chain: Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_journal_id,
                )
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $2
                    WHERE id = $1
                    """,
                    bt["journal_id"],
                    reversal_journal_id,
                )

                # Create reversal bank transactions (trigger handles balance)
                # Source bank: money comes back (positive = transfer_in)
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, journal_id, status, origin_type, source_module,
                        created_by, posted_by, posted_at
                    ) VALUES ($1, $2, $3, CURRENT_DATE, 'transfer_in', $4, 0, 'transfer_void', $5, $6, $7, $8,
                              'POSTED', 'SYSTEM', 'transfer', $9, $9, NOW())
                    """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    bt["from_bank_id"],
                    total_amount,  # Positive (money back)
                    transfer_id,
                    f"VOID-{bt['transfer_number']}",
                    f"Void transfer - {body.reason}",
                    reversal_journal_id,
                    ctx["user_id"],
                )

                # Destination bank: money goes out (negative = transfer_out)
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, journal_id, status, origin_type, source_module,
                        created_by, posted_by, posted_at
                    ) VALUES ($1, $2, $3, CURRENT_DATE, 'transfer_out', $4, 0, 'transfer_void', $5, $6, $7, $8,
                              'POSTED', 'SYSTEM', 'transfer', $9, $9, NOW())
                    """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    bt["to_bank_id"],
                    -amount,  # Negative (money out)
                    transfer_id,
                    f"VOID-{bt['transfer_number']}",
                    f"Void transfer - {body.reason}",
                    reversal_journal_id,
                    ctx["user_id"],
                )

                # Void original bank transactions
                if bt["from_transaction_id"]:
                    await conn.execute(
                        "UPDATE bank_transactions SET status = 'VOIDED', voided_by = $2, voided_at = NOW(), void_reason = $3 WHERE id = $1",
                        bt["from_transaction_id"],
                        ctx["user_id"],
                        body.reason,
                    )
                if bt["to_transaction_id"]:
                    await conn.execute(
                        "UPDATE bank_transactions SET status = 'VOIDED', voided_by = $2, voided_at = NOW(), void_reason = $3 WHERE id = $1",
                        bt["to_transaction_id"],
                        ctx["user_id"],
                        body.reason,
                    )

                # Update transfer status
                await conn.execute(
                    """
                    UPDATE bank_transfers
                    SET status = 'void', voided_at = NOW(), voided_by = $2,
                        voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                    """,
                    transfer_id,
                    ctx["user_id"],
                    body.reason,
                )

                logger.info(
                    f"Transfer voided: {transfer_id}, reversal={reversal_journal_id}"
                )

                return {
                    "success": True,
                    "message": "Bank transfer voided successfully",
                    "data": {
                        "id": str(transfer_id),
                        "status": "void",
                        "reversal_journal_id": str(reversal_journal_id),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding transfer {transfer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void transfer")
