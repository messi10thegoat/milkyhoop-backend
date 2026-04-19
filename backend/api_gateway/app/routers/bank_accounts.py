"""
Bank Accounts Router - Rekening Bank Management

Endpoints for managing bank accounts and viewing transactions.
Bank accounts are linked to Chart of Accounts for proper accounting integration.

Endpoints:
- GET    /bank-accounts                    - List all bank accounts
- GET    /bank-accounts/{id}               - Get bank account detail
- GET    /bank-accounts/{id}/transactions  - Transaction history
- GET    /bank-accounts/{id}/balance       - Balance info
- POST   /bank-accounts                    - Create bank account
- PATCH  /bank-accounts/{id}               - Update bank account
- DELETE /bank-accounts/{id}               - Soft delete (is_active=false)
- POST   /bank-accounts/{id}/adjust        - Manual balance adjustment
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import uuid as uuid_module

from ..schemas.bank_accounts import (
    CreateManualTransactionRequest,
    CreateBankAccountRequest,
    UpdateBankAccountRequest,
    AdjustBalanceRequest,
    BankAccountResponse,
    BankAccountDetailResponse,
    BankAccountListResponse,
    BankTransactionListResponse,
    BankAccountBalanceResponse,
)
from ..services.resolve_account import resolve_account_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool

# Account codes
OPENING_BALANCE_EQUITY = "3-50000"  # Modal Saldo Awal

# Parent CoA codes for auto-creation
BANK_PARENT_CODE = "1-10200"  # Aset - Bank (for bank, cash, petty_cash, e_wallet)
CREDIT_CARD_PARENT_CODE = "2-10600"  # Liabilitas - Hutang Kartu Kredit


async def get_next_coa_code(conn, tenant_id: str, prefix: str) -> str:
    """
    Generate next available CoA code for a bank account.

    Pattern: 1-10201, 1-10202, ..., 1-10299
             2-10601, 2-10602, ..., 2-10699

    Args:
        conn: Database connection
        tenant_id: Tenant ID
        prefix: Code prefix (e.g., "1-102" for bank, "2-106" for credit card)

    Returns:
        Next available CoA code (e.g., "1-10203")
    """
    # Find the last used code with this prefix (7 character codes like 1-10201)
    last_code = await conn.fetchval(
        """
        SELECT account_code FROM chart_of_accounts
        WHERE tenant_id = $1
        AND account_code LIKE $2
        AND LENGTH(account_code) = 7
        ORDER BY account_code DESC
        LIMIT 1
    """,
        tenant_id,
        f"{prefix}%",
    )

    if last_code:
        # Extract last 2 digits and increment
        next_num = int(last_code[-2:]) + 1
    else:
        # Start from 01
        next_num = 1

    if next_num > 99:
        raise ValueError(f"Maximum CoA codes reached for prefix {prefix}")

    return f"{prefix}{next_num:02d}"


async def auto_create_coa_for_bank_account(
    conn, tenant_id: str, account_name: str, account_type: str, user_id
) -> dict:
    """
    Auto-create a Chart of Accounts entry for a bank account.

    Args:
        conn: Database connection
        tenant_id: Tenant ID
        account_name: Bank account name (used for CoA name)
        account_type: Bank account type (bank, cash, credit_card, etc.)
        user_id: User creating the account

    Returns:
        Dict with coa_id and coa_code
    """
    # Determine CoA category based on account type
    if account_type == "credit_card":
        parent_code = CREDIT_CARD_PARENT_CODE
        code_prefix = "2-106"
        coa_type = "LIABILITY"
        normal_balance = "CREDIT"
    else:
        # bank, cash, petty_cash, e_wallet are all assets
        parent_code = BANK_PARENT_CODE
        code_prefix = "1-102"
        coa_type = "ASSET"
        normal_balance = "DEBIT"

    # Get parent CoA
    parent_coa = await conn.fetchrow(
        """
        SELECT id, account_code, name FROM chart_of_accounts
        WHERE account_code = $1 AND tenant_id = $2
    """,
        parent_code,
        tenant_id,
    )

    if not parent_coa:
        raise ValueError(
            f"Parent CoA {parent_code} not found for tenant {tenant_id}. "
            "Run migration V075 to add credit card parent or check chart_of_accounts setup."
        )

    # Generate next available code
    next_code = await get_next_coa_code(conn, tenant_id, code_prefix)

    # Create CoA entry
    coa_id = uuid_module.uuid4()
    await conn.execute(
        """
        INSERT INTO chart_of_accounts (
            id, tenant_id, account_code, name, description,
            is_cash,
            account_type, normal_balance, parent_code,
            is_header, is_active
        ) VALUES ($1, $2, $3, $4, $5, true, $6, $7, $8, false, true)
    """,
        coa_id,
        tenant_id,
        next_code,
        account_name,
        f"Auto-created for bank account: {account_name}",
        coa_type,
        normal_balance,
        parent_code,
    )

    return {"coa_id": coa_id, "coa_code": next_code, "coa_type": coa_type}


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


# =============================================================================
# LIST BANK ACCOUNTS
# =============================================================================


@router.get("", response_model=BankAccountListResponse)
async def list_bank_accounts(
    request: Request,
    is_active: Optional[bool] = Query(None),
    account_type: Optional[
        Literal["bank", "cash", "petty_cash", "e_wallet", "credit_card"]
    ] = Query(None),
    search: Optional[str] = Query(None, description="Search by name or account number"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["account_name", "current_balance", "created_at"] = Query(
        "account_name"
    ),
    sort_order: Literal["asc", "desc"] = Query("asc"),
):
    """List bank accounts with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["ba.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if is_active is not None:
                conditions.append(f"ba.is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            if account_type:
                conditions.append(f"ba.account_type = ${param_idx}")
                params.append(account_type)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(ba.account_name ILIKE ${param_idx} OR ba.account_number ILIKE ${param_idx} OR ba.bank_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(ba.account_name ILIKE ${param_idx} OR ba.account_number ILIKE ${param_idx} OR ba.bank_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "account_name": "ba.account_name",
                "current_balance": "lb.ledger_balance",  # Law 21: journal-derived balance
                "created_at": "ba.created_at",
            }
            sort_field = valid_sorts.get(sort_by, "ba.account_name")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM bank_accounts ba WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items with CoA info
            query = f"""
                SELECT ba.id, ba.account_name, ba.account_number, ba.bank_name,
                       ba.account_type, ba.coa_id,
                       ba.is_active, ba.is_default, ba.created_at,
                       coa.account_code as coa_code, coa.name as coa_name,
                       COALESCE(lb.ledger_balance, 0) as ledger_balance  -- Law 21: journal-derived balance
                FROM bank_accounts ba
                LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = ba.coa_id
                      AND je.status = 'POSTED'
                      AND je.tenant_id = $1
                ) lb ON true
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "account_name": row["account_name"],
                    "account_number": row["account_number"],
                    "bank_name": row["bank_name"],
                    "account_type": row["account_type"],
                    "coa_id": str(row["coa_id"]),
                    "coa_code": row["coa_code"],
                    "coa_name": row["coa_name"],
                    "current_balance": float(
                        row["ledger_balance"] or 0
                    ),  # Law 21: journal-derived
                    "ledger_balance": float(row["ledger_balance"] or 0),
                    "is_active": row["is_active"],
                    "is_default": row["is_default"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bank accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bank accounts")


# =============================================================================
# GET BANK ACCOUNT DETAIL
# BANK ACCOUNT DROPDOWN
# =============================================================================


@router.get("/dropdown")
async def get_bank_accounts_dropdown(request: Request):
    """Get bank accounts for dropdown/select components."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ba.id, ba.account_name, ba.account_number, ba.bank_name,
                       COALESCE(lb.ledger_balance, 0) as ledger_balance,
                       ba.currency
                FROM bank_accounts ba
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = ba.coa_id
                      AND je.status = 'POSTED'
                      AND je.tenant_id = $1
                ) lb ON true
                WHERE ba.tenant_id = $1 AND ba.is_active = true
                ORDER BY ba.account_name ASC
            """,
                ctx["tenant_id"],
            )

            accounts = [
                {
                    "id": str(row["id"]),
                    "name": row["account_name"],
                    "account_number": row["account_number"],
                    "bank_name": row["bank_name"],
                    "balance": float(
                        row["ledger_balance"] or 0
                    ),  # Law 21: journal-derived
                    "currency": row["currency"] or "IDR",
                }
                for row in rows
            ]

            return {"accounts": accounts}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting dropdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get accounts")


# =============================================================================


@router.get("/{bank_account_id}", response_model=BankAccountDetailResponse)
async def get_bank_account(request: Request, bank_account_id: UUID):
    """Get detailed information for a bank account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT ba.*, coa.account_code as coa_code, coa.name as coa_name,
                       COALESCE(lb.ledger_balance, 0) as ledger_balance
                FROM bank_accounts ba
                LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = ba.coa_id
                      AND je.status = 'POSTED'
                      AND je.tenant_id = $2
                ) lb ON true
                WHERE ba.id = $1 AND ba.tenant_id = $2
            """
            row = await conn.fetchrow(query, bank_account_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Get transaction count for this bank account
            # Count real transactions (exclude opening balance)
            transaction_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bank_transactions
                WHERE bank_account_id = $1 AND tenant_id = $2
                AND transaction_type != 'opening'
            """,
                bank_account_id,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "account_name": row["account_name"],
                    "account_number": row["account_number"],
                    "bank_name": row["bank_name"],
                    "bank_branch": row["bank_branch"],
                    "swift_code": row["swift_code"],
                    "account_type": row["account_type"],
                    "currency": row["currency"],
                    "coa_id": str(row["coa_id"]),
                    "coa_code": row["coa_code"],
                    "coa_name": row["coa_name"],
                    "opening_balance": row["opening_balance"] or 0,
                    "current_balance": float(
                        row["ledger_balance"] or 0
                    ),  # Law 21: journal-derived
                    "ledger_balance": float(row["ledger_balance"] or 0),
                    "last_reconciled_balance": row["last_reconciled_balance"] or 0,
                    "last_reconciled_date": row["last_reconciled_date"].isoformat()
                    if row["last_reconciled_date"]
                    else None,
                    "is_active": row["is_active"],
                    "is_default": row["is_default"],
                    "notes": row["notes"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                    "created_by": str(row["created_by"]) if row["created_by"] else None,
                    "transaction_count": transaction_count,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting bank account {bank_account_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get bank account")


# =============================================================================
# CREATE BANK ACCOUNT
# =============================================================================


@router.post("", response_model=BankAccountResponse, status_code=201)
async def create_bank_account(request: Request, body: CreateBankAccountRequest):
    """
    Create a new bank account.

    CoA handling:
    - If coa_id is provided: Use existing CoA (validated for correct type)
    - If coa_id is NOT provided: Auto-create CoA under appropriate parent:
      - bank/cash/petty_cash/e_wallet → 1-10200 Bank (ASSET)
      - credit_card → 2-10600 Hutang Kartu Kredit (LIABILITY)

    If opening_balance > 0, creates opening balance journal entry:
    - Dr. Bank Account (coa_id), Cr. Opening Balance Equity (for assets)
    - Dr. Opening Balance Equity, Cr. Credit Card (coa_id) (for credit cards)
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock for bank account creation
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_CREATE:{ctx['tenant_id']}:{body.account_name}",
                )

                coa_id = None
                coa_code = None
                coa_type = None

                if body.coa_id:
                    # Validate provided CoA exists and has correct type
                    coa = await conn.fetchrow(
                        """
                        SELECT id, account_code, name, account_type
                        FROM chart_of_accounts
                        WHERE id = $1 AND tenant_id = $2
                    """,
                        UUID(body.coa_id),
                        ctx["tenant_id"],
                    )

                    if not coa:
                        raise HTTPException(
                            status_code=400, detail="Chart of Accounts entry not found"
                        )

                    # Validate CoA type matches account type
                    if body.account_type == "credit_card":
                        if coa["account_type"] != "LIABILITY":
                            raise HTTPException(
                                status_code=400,
                                detail="Credit card account must be linked to a LIABILITY type CoA",
                            )
                    else:
                        if coa["account_type"] != "ASSET":
                            raise HTTPException(
                                status_code=400,
                                detail="Bank account must be linked to an ASSET type CoA (e.g., Kas or Bank)",
                            )

                    coa_id = UUID(body.coa_id)
                    coa_code = coa["account_code"]
                    coa_type = coa["account_type"]
                else:
                    # Auto-create CoA for bank account
                    coa_result = await auto_create_coa_for_bank_account(
                        conn,
                        ctx["tenant_id"],
                        body.account_name,
                        body.account_type,
                        ctx["user_id"],
                    )
                    coa_id = coa_result["coa_id"]
                    coa_code = coa_result["coa_code"]
                    coa_type = coa_result["coa_type"]
                    logger.info(
                        f"Auto-created CoA {coa_code} for bank account {body.account_name}"
                    )

                # Check name uniqueness
                existing = await conn.fetchval(
                    """
                    SELECT id FROM bank_accounts
                    WHERE tenant_id = $1 AND account_name = $2
                """,
                    ctx["tenant_id"],
                    body.account_name,
                )

                if existing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Bank account with name '{body.account_name}' already exists",
                    )

                # Check CoA not already linked (only for existing CoAs, auto-created ones are unique)
                if body.coa_id:
                    existing_coa = await conn.fetchval(
                        """
                        SELECT id FROM bank_accounts
                        WHERE tenant_id = $1 AND coa_id = $2
                    """,
                        ctx["tenant_id"],
                        coa_id,
                    )

                    if existing_coa:
                        raise HTTPException(
                            status_code=400,
                            detail="This CoA is already linked to another bank account",
                        )

                # Handle is_default - only one default allowed
                if body.is_default:
                    await conn.execute(
                        """
                        UPDATE bank_accounts
                        SET is_default = false, updated_at = NOW()
                        WHERE tenant_id = $1 AND is_default = true
                    """,
                        ctx["tenant_id"],
                    )

                # Create bank account
                # Law 21: current_balance column retained but deprecated. Balance computed from journal.
                bank_account_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO bank_accounts (
                        id, tenant_id, account_name, account_number, bank_name, bank_branch,
                        swift_code, coa_id, opening_balance, current_balance,  -- Law 21: current_balance column retained but deprecated. Balance computed from journal.
                        account_type, currency, is_default, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10, $11, $12, $13, $14)
                """,
                    bank_account_id,
                    ctx["tenant_id"],
                    body.account_name,
                    body.account_number,
                    body.bank_name,
                    body.bank_branch,
                    body.swift_code,
                    coa_id,  # Use resolved coa_id (either from body or auto-created)
                    body.opening_balance,
                    body.account_type,
                    body.currency,
                    body.is_default,
                    body.notes,
                    ctx["user_id"],
                )

                journal_id = None

                # Create opening balance entry if > 0
                if body.opening_balance > 0:
                    journal_id = uuid_module.uuid4()
                    trace_id = uuid_module.uuid4()
                    opening_date = body.opening_date or date.today()

                    # Law 27: Use resolve_account_id helper
                    equity_account_id = await resolve_account_id(
                        conn, ctx["tenant_id"], OPENING_BALANCE_EQUITY
                    )
                    if not equity_account_id:
                        raise HTTPException(
                            status_code=500,
                            detail="Opening Balance Equity account not found in CoA",
                        )

                    # Create journal entry
                    journal_number = f"OB-BA-{body.account_name[:10]}"

                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'OPENING', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                        journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        opening_date,
                        f"Opening Balance - {body.account_name}",
                        bank_account_id,
                        str(trace_id),
                        int(body.opening_balance),
                        ctx["user_id"],
                    )

                    # Journal entries depend on account type
                    # ASSET (bank, cash): Dr. Bank, Cr. Equity
                    # LIABILITY (credit_card): Dr. Equity, Cr. Credit Card
                    if coa_type == "LIABILITY":
                        # Credit card: Dr. Equity, Cr. Credit Card (increase liability)
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                            uuid_module.uuid4(),
                            journal_id,
                            equity_account_id,
                            int(body.opening_balance),
                            f"Modal Saldo Awal - {body.account_name}",
                        )
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                            uuid_module.uuid4(),
                            journal_id,
                            coa_id,
                            int(body.opening_balance),
                            f"Saldo Awal Hutang - {body.account_name}",
                        )
                    else:
                        # Asset (bank, cash, etc): Dr. Bank, Cr. Equity
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                            uuid_module.uuid4(),
                            journal_id,
                            coa_id,
                            int(body.opening_balance),
                            f"Saldo Awal - {body.account_name}",
                        )
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                            uuid_module.uuid4(),
                            journal_id,
                            equity_account_id,
                            int(body.opening_balance),
                            f"Modal Saldo Awal - {body.account_name}",
                        )

                    # Law 20: DRAFT→POSTED for hash chain
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        journal_id,
                    )

                    # Create opening bank transaction
                    # Note: Trigger now handles balance update atomically (V076)
                    # For opening balance, running_balance will be set by trigger
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date, transaction_type,
                            amount, running_balance, reference_type, reference_id,
                            description, journal_id, created_by
                        ) VALUES ($1, $2, $3, $4, 'opening', $5, 0, 'opening_balance', $6, $7, $8, $9)
                    """,
                        uuid_module.uuid4(),
                        ctx["tenant_id"],
                        bank_account_id,
                        opening_date,
                        body.opening_balance,
                        bank_account_id,
                        f"Saldo Awal - {body.account_name}",
                        journal_id,
                        ctx["user_id"],
                    )

                logger.info(
                    f"Bank account created: {bank_account_id}, coa_code={coa_code}"
                )

                return {
                    "success": True,
                    "message": "Bank account created successfully",
                    "data": {
                        "id": str(bank_account_id),
                        "account_name": body.account_name,
                        "coa_id": str(coa_id),
                        "coa_code": coa_code,
                        "account_type": body.account_type,
                        "opening_balance": body.opening_balance,
                        "journal_id": str(journal_id) if journal_id else None,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating bank account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create bank account")


# =============================================================================
# UPDATE BANK ACCOUNT
# =============================================================================


@router.patch("/{bank_account_id}", response_model=BankAccountResponse)
async def update_bank_account(
    request: Request, bank_account_id: UUID, body: UpdateBankAccountRequest
):
    """Update a bank account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock for bank account deletion
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_DELETE:{bank_account_id}",
                )

                # Get existing account
                ba = await conn.fetchrow(
                    """
                    SELECT * FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    bank_account_id,
                    ctx["tenant_id"],
                )

                if not ba:
                    raise HTTPException(
                        status_code=404, detail="Bank account not found"
                    )

                # Build update
                updates = []
                params = []
                param_idx = 1

                if body.account_name is not None:
                    # Check uniqueness
                    existing = await conn.fetchval(
                        """
                        SELECT id FROM bank_accounts
                        WHERE tenant_id = $1 AND account_name = $2 AND id != $3
                    """,
                        ctx["tenant_id"],
                        body.account_name,
                        bank_account_id,
                    )
                    if existing:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Bank account with name '{body.account_name}' already exists",
                        )
                    updates.append(f"account_name = ${param_idx}")
                    params.append(body.account_name)
                    param_idx += 1

                if body.account_number is not None:
                    updates.append(f"account_number = ${param_idx}")
                    params.append(body.account_number)
                    param_idx += 1

                if body.bank_name is not None:
                    updates.append(f"bank_name = ${param_idx}")
                    params.append(body.bank_name)
                    param_idx += 1

                if body.bank_branch is not None:
                    updates.append(f"bank_branch = ${param_idx}")
                    params.append(body.bank_branch)
                    param_idx += 1

                if body.swift_code is not None:
                    updates.append(f"swift_code = ${param_idx}")
                    params.append(body.swift_code)
                    param_idx += 1

                if body.is_active is not None:
                    updates.append(f"is_active = ${param_idx}")
                    params.append(body.is_active)
                    param_idx += 1

                if body.is_default is not None:
                    if body.is_default:
                        # Clear other defaults first
                        await conn.execute(
                            """
                            UPDATE bank_accounts
                            SET is_default = false, updated_at = NOW()
                            WHERE tenant_id = $1 AND is_default = true AND id != $2
                        """,
                            ctx["tenant_id"],
                            bank_account_id,
                        )
                    updates.append(f"is_default = ${param_idx}")
                    params.append(body.is_default)
                    param_idx += 1

                if body.notes is not None:
                    updates.append(f"notes = ${param_idx}")
                    params.append(body.notes)
                    param_idx += 1

                if not updates:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(bank_account_id)},
                    }

                updates.append("updated_at = NOW()")
                params.extend([bank_account_id, ctx["tenant_id"]])

                query = f"""
                    UPDATE bank_accounts
                    SET {", ".join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

                logger.info(f"Bank account updated: {bank_account_id}")

                return {
                    "success": True,
                    "message": "Bank account updated successfully",
                    "data": {"id": str(bank_account_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error updating bank account {bank_account_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to update bank account")


# =============================================================================
# DELETE (SOFT) BANK ACCOUNT
# =============================================================================


@router.delete("/{bank_account_id}", response_model=BankAccountResponse)
async def delete_bank_account(request: Request, bank_account_id: UUID):
    """
    Delete a bank account.

    - If account has 0 transactions: HARD DELETE (removes bank_account and related CoA)
    - If account has transactions: SOFT DELETE (sets is_active = false)
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get existing account
                ba = await conn.fetchrow(
                    """
                    SELECT * FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    bank_account_id,
                    ctx["tenant_id"],
                )

                if not ba:
                    raise HTTPException(
                        status_code=404, detail="Bank account not found"
                    )

                # Check if has REAL transactions (exclude opening balance - that's just initialization)
                tx_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM bank_transactions
                    WHERE bank_account_id = $1 AND tenant_id = $2
                    AND transaction_type != 'opening'
                """,
                    bank_account_id,
                    ctx["tenant_id"],
                )

                if tx_count > 0:
                    # Soft delete only - account has transaction history
                    if not ba["is_active"]:
                        raise HTTPException(
                            status_code=400, detail="Bank account already inactive"
                        )

                    await conn.execute(
                        """
                        UPDATE bank_accounts
                        SET is_active = false, is_default = false, updated_at = NOW()
                        WHERE id = $1
                    """,
                        bank_account_id,
                    )

                    logger.info(
                        f"Bank account soft deleted (has {tx_count} transactions): {bank_account_id}"
                    )

                    return {
                        "success": True,
                        "message": f"Bank account deactivated (has {tx_count} transactions)",
                        "data": {
                            "id": str(bank_account_id),
                            "is_active": False,
                            "hard_deleted": False,
                        },
                    }
                else:
                    # HARD DELETE - no real transactions, safe to permanently remove
                    coa_id = ba["coa_id"]
                    account_name = ba["account_name"]

                    # Delete opening balance journal lines first (FK to journal_entries)
                    await conn.execute(
                        """
                        DELETE FROM journal_lines
                        WHERE journal_id IN (
                            SELECT id FROM journal_entries
                            WHERE source_type = 'OPENING'
                            AND source_id = $1
                            AND tenant_id = $2
                        )
                    """,
                        bank_account_id,
                        ctx["tenant_id"],
                    )

                    # Delete opening balance journal entries
                    await conn.execute(
                        """
                        DELETE FROM journal_entries
                        WHERE source_type = 'OPENING'
                        AND source_id = $1
                        AND tenant_id = $2
                    """,
                        bank_account_id,
                        ctx["tenant_id"],
                    )

                    # Delete opening balance bank transactions
                    await conn.execute(
                        """
                        DELETE FROM bank_transactions
                        WHERE bank_account_id = $1
                        AND tenant_id = $2
                        AND transaction_type = 'opening'
                    """,
                        bank_account_id,
                        ctx["tenant_id"],
                    )

                    # Delete reconciliation data (FK cascade: adjustments/matches/lines → sessions → bank_accounts)
                    recon_session_ids = [
                        r["id"]
                        for r in await conn.fetch(
                            "SELECT id FROM reconciliation_sessions WHERE account_id = $1 AND tenant_id = $2",
                            bank_account_id,
                            ctx["tenant_id"],
                        )
                    ]
                    if recon_session_ids:
                        await conn.execute(
                            "DELETE FROM reconciliation_adjustments WHERE session_id = ANY($1::uuid[])",
                            recon_session_ids,
                        )
                        await conn.execute(
                            "DELETE FROM reconciliation_matches WHERE session_id = ANY($1::uuid[])",
                            recon_session_ids,
                        )
                        await conn.execute(
                            "DELETE FROM bank_statement_lines_v2 WHERE session_id = ANY($1::uuid[])",
                            recon_session_ids,
                        )
                        await conn.execute(
                            "DELETE FROM reconciliation_sessions WHERE account_id = $1 AND tenant_id = $2",
                            bank_account_id,
                            ctx["tenant_id"],
                        )

                    # Delete the bank account (references coa_id)
                    await conn.execute(
                        """
                        DELETE FROM bank_accounts
                        WHERE id = $1 AND tenant_id = $2
                    """,
                        bank_account_id,
                        ctx["tenant_id"],
                    )

                    # Delete the related CoA entry
                    if coa_id:
                        await conn.execute(
                            """
                            DELETE FROM chart_of_accounts
                            WHERE id = $1 AND tenant_id = $2
                        """,
                            coa_id,
                            ctx["tenant_id"],
                        )

                    logger.info(
                        f"Bank account hard deleted with opening balance cleanup: {bank_account_id}, CoA: {coa_id}"
                    )

                    return {
                        "success": True,
                        "message": f"Bank account '{account_name}' permanently deleted",
                        "data": {"id": str(bank_account_id), "hard_deleted": True},
                    }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error deleting bank account {bank_account_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to delete bank account")


# =============================================================================
# GET TRANSACTIONS
# =============================================================================


@router.get(
    "/{bank_account_id}/transactions", response_model=BankTransactionListResponse
)
async def get_bank_transactions(
    request: Request,
    bank_account_id: UUID,
    transaction_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    is_reconciled: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Get transaction history for a bank account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify bank account exists and get coa_id for journal lookups
            ba = await conn.fetchrow(
                """
                SELECT id, coa_id FROM bank_accounts
                WHERE id = $1 AND tenant_id = $2
            """,
                bank_account_id,
                ctx["tenant_id"],
            )

            if not ba:
                raise HTTPException(status_code=404, detail="Bank account not found")

            coa_id = ba["coa_id"]

            # Build query
            conditions = ["bt.bank_account_id = $1"]
            params = [bank_account_id]
            param_idx = 2

            if transaction_type:
                conditions.append(f"bt.transaction_type = ${param_idx}")
                params.append(transaction_type)
                param_idx += 1

            if date_from:
                conditions.append(f"bt.transaction_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"bt.transaction_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            if is_reconciled is not None:
                conditions.append(f"bt.is_reconciled = ${param_idx}")
                params.append(is_reconciled)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count total
            count_query = (
                f"SELECT COUNT(*) FROM bank_transactions bt WHERE {where_clause}"
            )
            total = await conn.fetchval(count_query, *params)

            # Law 1: Journal-derived amounts via LEFT JOIN to journal_lines
            # If bt.journal_id exists, use journal-derived amount; otherwise fallback to bt.amount
            # Running balance computed as window function over journal-derived amounts
            coa_param = f"${param_idx}"
            params.append(coa_id)
            param_idx += 1
            tenant_param = f"${param_idx}"
            params.append(ctx["tenant_id"])
            param_idx += 1

            query = f"""
                WITH txn_data AS (
                    SELECT bt.id, bt.transaction_date, bt.transaction_type,
                           -- Law 1: prefer journal-derived amount, fallback for unreconciled imports
                           CASE WHEN bt.journal_id IS NOT NULL AND jl.journal_id IS NOT NULL
                               THEN COALESCE(jl.debit, 0) - COALESCE(jl.credit, 0)
                               ELSE bt.amount
                           END as amount,
                           bt.description, bt.payee_payer, bt.reference_type, bt.reference_number,
                           bt.is_reconciled, bt.created_at, bt.source_module,
                           -- Enrich: customer name from sales_invoices via reference_id
                           si.invoice_number AS related_invoice_number,
                           c.nama AS customer_name,
                           -- Enrich: vendor name from bills via reference_id
                           b.invoice_number AS related_bill_number,
                           v.name AS vendor_name
                    FROM bank_transactions bt
                    LEFT JOIN bank_accounts ba_coa ON ba_coa.id = bt.bank_account_id
                    LEFT JOIN journal_lines jl ON jl.journal_id = bt.journal_id AND jl.account_id = {coa_param}
                    LEFT JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = {tenant_param} AND je.status = 'POSTED'
                    LEFT JOIN sales_invoices si ON bt.reference_type = 'invoice' AND si.id = bt.reference_id
                    LEFT JOIN customers c ON si.customer_id = c.id
                    LEFT JOIN bills b ON bt.reference_type = 'bill' AND b.id = bt.reference_id
                    LEFT JOIN vendors v ON b.vendor_id = v.id
                    WHERE {where_clause}
                )
                SELECT *,
                    SUM(amount) OVER (ORDER BY transaction_date ASC, created_at ASC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as running_balance
                FROM txn_data
                ORDER BY transaction_date DESC, created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "transaction_date": row["transaction_date"].isoformat(),
                    "transaction_type": row["transaction_type"],
                    "amount": row["amount"],
                    "running_balance": row["running_balance"],
                    "description": row["description"],
                    "payee_payer": row["payee_payer"],
                    "reference_type": row["reference_type"],
                    "reference_number": row["reference_number"],
                    "is_reconciled": row["is_reconciled"],
                    "created_at": row["created_at"].isoformat(),
                    "source_module": row.get("source_module"),
                    "customer_name": row.get("customer_name"),
                    "vendor_name": row.get("vendor_name"),
                    "related_invoice_number": row.get("related_invoice_number"),
                    "related_bill_number": row.get("related_bill_number"),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting transactions for {bank_account_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get transactions")


# =============================================================================
# GET BALANCE INFO
# =============================================================================


@router.get("/{bank_account_id}/balance", response_model=BankAccountBalanceResponse)
async def get_bank_balance(request: Request, bank_account_id: UUID):
    """Get balance information for a bank account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get bank account
            ba = await conn.fetchrow(
                """
                SELECT ba.id, ba.account_name, ba.opening_balance,
                       COALESCE(lb.ledger_balance, 0) as ledger_balance  -- Law 21: journal-derived balance
                FROM bank_accounts ba
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = ba.coa_id
                      AND je.status = 'POSTED'
                      AND je.tenant_id = $2
                ) lb ON true
                WHERE ba.id = $1 AND ba.tenant_id = $2
            """,
                bank_account_id,
                ctx["tenant_id"],
            )

            if not ba:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Law 1: Journal-derived deposits/withdrawals from journal_lines
            journal_stats = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(jl.debit), 0) as total_deposits,
                    COALESCE(SUM(jl.credit), 0) as total_withdrawals,
                    COUNT(*) as journal_txn_count,
                    MAX(je.journal_date) as last_journal_date
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE jl.account_id = (SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2)
                    AND je.tenant_id = $2 AND je.status = 'POSTED'
                    AND je.reversed_by_id IS NULL
            """,
                bank_account_id,
                ctx["tenant_id"],
            )

            # Keep bank_transactions stats for reconciliation counts only
            recon_stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as transaction_count,
                    COUNT(*) FILTER (WHERE is_reconciled = false) as unreconciled_count
                FROM bank_transactions
                WHERE bank_account_id = $1
            """,
                bank_account_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(ba["id"]),
                    "account_name": ba["account_name"],
                    "opening_balance": ba["opening_balance"] or 0,
                    "current_balance": float(
                        ba["ledger_balance"] or 0
                    ),  # Law 21: journal-derived
                    "ledger_balance": float(ba["ledger_balance"] or 0),
                    "total_deposits": float(
                        journal_stats["total_deposits"] or 0
                    ),  # Law 1: journal-derived
                    "total_withdrawals": float(
                        journal_stats["total_withdrawals"] or 0
                    ),  # Law 1: journal-derived
                    "transaction_count": recon_stats["transaction_count"] or 0,
                    "unreconciled_count": recon_stats["unreconciled_count"] or 0,
                    "last_transaction_date": journal_stats[
                        "last_journal_date"
                    ].isoformat()
                    if journal_stats["last_journal_date"]
                    else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting balance for {bank_account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get balance")


# =============================================================================
# MANUAL ADJUSTMENT
# =============================================================================


@router.post("/{bank_account_id}/adjust", response_model=BankAccountResponse)
async def adjust_bank_balance(
    request: Request, bank_account_id: UUID, body: AdjustBalanceRequest
):
    """
    Make a manual balance adjustment.

    Creates journal entry:
    - If positive: Dr. Bank Account, Cr. Adjustment Income
    - If negative: Dr. Adjustment Expense, Cr. Bank Account
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock for balance adjustment
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_ADJUST:{bank_account_id}",
                )

                # Get bank account
                ba = await conn.fetchrow(
                    """
                    SELECT ba.*, coa.id as coa_account_id,
                           COALESCE(lb.ledger_balance, 0) as ledger_balance
                    FROM bank_accounts ba
                    LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
                    LEFT JOIN LATERAL (
                        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE jl.account_id = ba.coa_id
                          AND je.status = 'POSTED'
                          AND je.tenant_id = $2
                    ) lb ON true
                    WHERE ba.id = $1 AND ba.tenant_id = $2
                """,
                    bank_account_id,
                    ctx["tenant_id"],
                )

                if not ba:
                    raise HTTPException(
                        status_code=404, detail="Bank account not found"
                    )

                if not ba["is_active"]:
                    raise HTTPException(
                        status_code=400, detail="Cannot adjust inactive bank account"
                    )

                adjustment = body.adjustment_amount
                if adjustment == 0:
                    raise HTTPException(
                        status_code=400, detail="Adjustment amount cannot be zero"
                    )

                current_balance = float(
                    ba["ledger_balance"] or 0
                )  # Law 21: journal-derived balance
                new_balance = current_balance + adjustment

                if new_balance < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Adjustment would result in negative balance ({new_balance})",
                    )

                # Create journal entry
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = f"ADJ-BA-{uuid_module.uuid4().hex[:8].upper()}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'ADJUSTMENT', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.adjustment_date,
                    f"Bank Adjustment - {ba['account_name']}: {body.reason}",
                    bank_account_id,
                    str(trace_id),
                    int(abs(adjustment)),
                    ctx["user_id"],
                )

                # For simplicity, use the bank account itself as both debit/credit
                # In production, you'd want separate adjustment income/expense accounts
                if adjustment > 0:
                    # Dr. Bank Account
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        ba["coa_id"],
                        int(adjustment),
                        f"Penyesuaian Saldo - {body.reason}",
                    )
                    # Cr. Opening Balance Equity (as adjustment source)
                    # Law 27: Use resolve_account_id helper
                    equity_id = await resolve_account_id(
                        conn, ctx["tenant_id"], OPENING_BALANCE_EQUITY
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        equity_id,
                        int(adjustment),
                        f"Koreksi Saldo - {body.reason}",
                    )
                else:
                    # Dr. Opening Balance Equity
                    # Law 27: Use resolve_account_id helper
                    equity_id = await resolve_account_id(
                        conn, ctx["tenant_id"], OPENING_BALANCE_EQUITY
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        equity_id,
                        int(abs(adjustment)),
                        f"Koreksi Saldo - {body.reason}",
                    )
                    # Cr. Bank Account
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        ba["coa_id"],
                        int(abs(adjustment)),
                        f"Penyesuaian Saldo - {body.reason}",
                    )

                # Law 20: DRAFT→POSTED for hash chain
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Create bank transaction (trigger will update balance)
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id,
                        description, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, 'adjustment', $5, $6, 'adjustment', $7, $8, $9, $10)
                """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    bank_account_id,
                    body.adjustment_date,
                    adjustment,
                    new_balance,
                    bank_account_id,
                    body.reason,
                    journal_id,
                    ctx["user_id"],
                )

                logger.info(
                    f"Bank account adjusted: {bank_account_id}, amount={adjustment}"
                )

                return {
                    "success": True,
                    "message": "Bank account balance adjusted",
                    "data": {
                        "id": str(bank_account_id),
                        "adjustment_amount": adjustment,
                        "new_balance": new_balance,
                        "journal_id": str(journal_id),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error adjusting bank account {bank_account_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to adjust bank account")


# =============================================================================
# BANK ACCOUNT TRANSACTIONS
# =============================================================================


@router.get("/{bank_account_id}/statement")
async def get_bank_account_statement(
    request: Request,
    bank_account_id: str,
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
):
    """Get a statement-style report for a bank account.

    Law 1: All amounts derived from journal_lines via the bank account's coa_id.
    """
    # Parse date strings to date objects
    from datetime import datetime

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Get bank account with coa_id for journal lookups
            account = await conn.fetchrow(
                "SELECT id, account_name, coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                bank_account_id,
                ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Bank account not found")

            coa_id = account["coa_id"]

            # Law 1: Opening balance from journal_lines (all POSTED entries before start_date)
            opening = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE jl.account_id = $1
                    AND je.tenant_id = $2
                    AND je.status = 'POSTED'
                    AND je.journal_date < $3
                    AND je.reversed_by_id IS NULL
            """,
                coa_id,
                ctx["tenant_id"],
                start_dt,
            )

            # Law 1: Transactions in period from journal_lines
            # debit = deposit (money in), credit = withdrawal (money out) for asset accounts
            # Net amount = debit - credit (positive = deposit, negative = withdrawal)
            rows = await conn.fetch(
                """
                SELECT je.id::text, je.source_type as type, je.description,
                    je.journal_date as date, je.source_id,
                    COALESCE(jl.debit, 0) as deposit,
                    COALESCE(jl.credit, 0) as withdrawal,
                    COALESCE(jl.debit, 0) - COALESCE(jl.credit, 0) as amount
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                WHERE jl.account_id = $1
                    AND je.tenant_id = $2
                    AND je.status = 'POSTED'
                    AND je.journal_date BETWEEN $3 AND $4
                    AND je.reversed_by_id IS NULL
                ORDER BY je.journal_date ASC, je.created_at ASC
            """,
                coa_id,
                ctx["tenant_id"],
                start_dt,
                end_dt,
            )

            transactions = []
            running_balance = float(opening or 0)
            for row in rows:
                amount = float(row["amount"])
                running_balance += amount
                transactions.append(
                    {
                        "id": row["id"],
                        "type": row["type"] or "journal",
                        "reference": row["type"] or "",
                        "date": row["date"].isoformat() if row["date"] else None,
                        "amount": amount,
                        "description": row["description"] or "",
                        "running_balance": running_balance,
                    }
                )

            total_deposits = sum(t["amount"] for t in transactions if t["amount"] > 0)
            total_withdrawals = sum(
                abs(t["amount"]) for t in transactions if t["amount"] < 0
            )

            return {
                "account_name": account["account_name"],
                "start_date": start_date,
                "end_date": end_date,
                "opening_balance": float(opening or 0),
                "closing_balance": running_balance,
                "total_deposits": total_deposits,
                "total_withdrawals": total_withdrawals,
                "transactions": transactions,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting statement: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get statement")


# =============================================================================


# =============================================================================
# CREATE MANUAL TRANSACTION (Uang Masuk / Uang Keluar)
# =============================================================================


@router.post("/{bank_account_id}/transactions")
async def create_manual_transaction(
    request: Request,
    bank_account_id: UUID,
    body: CreateManualTransactionRequest,
):
    """
    Create manual bank transaction (Uang Masuk / Uang Keluar).

    BankSync Rule 1: journal (DRAFT→POSTED) + bank_transaction atomic in 1 DB transaction.
    Law 13: Advisory lock BANK_TX_MANUAL:{bank_account_id} acquired first.
    Law 20: DRAFT→POSTED pattern (triggers hash chain).
    Law 27: Bank CoA resolved from bank_accounts.coa_id.
    Law 29: Rejects RECEIVABLE/PAYABLE contra — route to correct module.
    BankSync Rule 10: origin_type = 'MANUAL'.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock — FIRST before any reads/writes
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TX_MANUAL:{bank_account_id}",
                )

                # Verify bank account belongs to tenant
                ba = await conn.fetchrow(
                    """
                    SELECT id, coa_id, account_name, is_active
                    FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    bank_account_id,
                    ctx["tenant_id"],
                )
                if not ba:
                    raise HTTPException(
                        status_code=404, detail="Bank account not found"
                    )
                if not ba["is_active"]:
                    raise HTTPException(
                        status_code=400, detail="Bank account is inactive"
                    )

                # Law 27: CoA from bank_accounts.coa_id (not hardcoded)
                bank_coa_id = ba["coa_id"]

                # Law 29: Reject RECEIVABLE/PAYABLE contra — route to correct module
                contra_coa = await conn.fetchrow(
                    """
                    SELECT id, account_type FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    uuid_module.UUID(body.contra_account_id),
                    ctx["tenant_id"],
                )
                if not contra_coa:
                    raise HTTPException(
                        status_code=400, detail="Contra account not found"
                    )
                if contra_coa["account_type"] in ("RECEIVABLE", "PAYABLE"):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Gunakan modul Terima Pembayaran untuk piutang, "
                            "atau Bayar Tagihan untuk hutang. (Law 29)"
                        ),
                    )

                # Law 5: Period check
                period = await conn.fetchrow(
                    """
                    SELECT id, period_name, status FROM fiscal_periods
                    WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
                    ORDER BY start_date DESC LIMIT 1
                    """,
                    ctx["tenant_id"],
                    body.transaction_date,
                )
                if period and period["status"] in ("CLOSED", "LOCKED"):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
                    )

                # Compute current ledger balance (for running_balance cache)
                current_balance = (
                    await conn.fetchval(
                        """
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = $1 AND je.status = 'POSTED' AND je.tenant_id = $2
                    """,
                        bank_coa_id,
                        ctx["tenant_id"],
                    )
                    or 0
                )

                amount = body.amount
                txn_amount = amount if body.direction == "in" else -amount
                running_balance = int(current_balance) + txn_amount

                # Law 20, Gate 3: Create DRAFT journal
                journal_id = uuid_module.uuid4()
                journal_number = f"MT-{uuid_module.uuid4().hex[:8].upper()}"
                direction_label = (
                    "Uang Masuk" if body.direction == "in" else "Uang Keluar"
                )

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'BANK_TRANSACTION', $6, 'DRAFT', $7, $7, $8)
                    """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.transaction_date,
                    f"{direction_label} - {body.description}",
                    str(bank_account_id),  # temp source_id, updated after bank_txn
                    amount,
                    ctx.get("user_id"),
                )

                # Law 4: Insert journal lines (debit = credit)
                contra_uuid = uuid_module.UUID(body.contra_account_id)
                if body.direction == "in":
                    # Uang Masuk: Dr. Bank CoA, Cr. Contra
                    line1 = (
                        uuid_module.uuid4(),
                        journal_id,
                        1,
                        bank_coa_id,
                        amount,
                        0,
                        body.description,
                    )
                    line2 = (
                        uuid_module.uuid4(),
                        journal_id,
                        2,
                        contra_uuid,
                        0,
                        amount,
                        body.description,
                    )
                else:
                    # Uang Keluar: Dr. Contra, Cr. Bank CoA
                    line1 = (
                        uuid_module.uuid4(),
                        journal_id,
                        1,
                        contra_uuid,
                        amount,
                        0,
                        body.description,
                    )
                    line2 = (
                        uuid_module.uuid4(),
                        journal_id,
                        2,
                        bank_coa_id,
                        0,
                        amount,
                        body.description,
                    )

                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    *line1,
                )
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    *line2,
                )

                # Law 20: UPDATE to POSTED — triggers hash chain
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # BankSync Rule 1: bank_transaction atomic with journal
                # BankSync Rule 10: origin_type = 'MANUAL' (user-initiated)
                bank_txn_id = uuid_module.uuid4()
                txn_type = "deposit" if body.direction == "in" else "withdrawal"

                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, description, payee_payer,
                        origin_type, status, journal_id, created_by
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'MANUAL','POSTED',$10,$11)
                    """,
                    bank_txn_id,
                    ctx["tenant_id"],
                    bank_account_id,
                    body.transaction_date,
                    txn_type,
                    txn_amount,
                    running_balance,
                    body.description,
                    body.contact_name,
                    journal_id,
                    ctx.get("user_id"),
                )

                # Law 6: Update journal source_id to actual bank_transaction id
                await conn.execute(
                    "UPDATE journal_entries SET source_id = $1 WHERE id = $2",
                    str(bank_txn_id),
                    journal_id,
                )

                logger.info(
                    f"Manual transaction created: {bank_txn_id}, "
                    f"direction={body.direction}, amount={amount}, "
                    f"account={bank_account_id}"
                )

                return {
                    "success": True,
                    "data": {
                        "id": str(bank_txn_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "direction": body.direction,
                        "amount": amount,
                        "transaction_date": body.transaction_date.isoformat(),
                        "description": body.description,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating manual transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
