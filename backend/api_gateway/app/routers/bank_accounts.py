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
    CreateBankAccountRequest,
    UpdateBankAccountRequest,
    AdjustBalanceRequest,
    BankAccountResponse,
    BankAccountDetailResponse,
    BankAccountListResponse,
    BankTransactionListResponse,
    BankAccountBalanceResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

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
            account_type, normal_balance, parent_code,
            is_header, is_active
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, false, true)
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
                conditions.append(
                    f"(ba.account_name ILIKE ${param_idx} OR ba.account_number ILIKE ${param_idx} OR ba.bank_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "account_name": "ba.account_name",
                "current_balance": "ba.current_balance",
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
                       ba.account_type, ba.coa_id, ba.current_balance,
                       ba.is_active, ba.is_default, ba.created_at,
                       coa.account_code as coa_code, coa.name as coa_name
                FROM bank_accounts ba
                LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
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
                    "current_balance": row["current_balance"] or 0,
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
                SELECT id, account_name, account_number, bank_name, current_balance, currency
                FROM bank_accounts
                WHERE tenant_id = $1 AND is_active = true
                ORDER BY account_name ASC
            """,
                ctx["tenant_id"],
            )

            accounts = [
                {
                    "id": str(row["id"]),
                    "name": row["account_name"],
                    "account_number": row["account_number"],
                    "bank_name": row["bank_name"],
                    "balance": row["current_balance"],
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
                SELECT ba.*, coa.account_code as coa_code, coa.name as coa_name
                FROM bank_accounts ba
                LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
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
                    "current_balance": row["current_balance"] or 0,
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
                bank_account_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO bank_accounts (
                        id, tenant_id, account_name, account_number, bank_name, bank_branch,
                        swift_code, coa_id, opening_balance, current_balance,
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

                    # Get opening balance equity account
                    equity_account_id = await conn.fetchval(
                        """
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1 AND account_code = $2
                    """,
                        ctx["tenant_id"],
                        OPENING_BALANCE_EQUITY,
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
                        ) VALUES ($1, $2, $3, $4, $5, 'OPENING', $6, $7, 'POSTED', $8, $8, $9)
                    """,
                        journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        opening_date,
                        f"Opening Balance - {body.account_name}",
                        bank_account_id,
                        str(trace_id),
                        float(body.opening_balance),
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
                            float(body.opening_balance),
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
                            float(body.opening_balance),
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
                            float(body.opening_balance),
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
                            float(body.opening_balance),
                            f"Modal Saldo Awal - {body.account_name}",
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
                    raise HTTPException(status_code=404, detail="Bank account not found")

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

                    logger.info(f"Bank account soft deleted (has {tx_count} transactions): {bank_account_id}")

                    return {
                        "success": True,
                        "message": f"Bank account deactivated (has {tx_count} transactions)",
                        "data": {"id": str(bank_account_id), "is_active": False, "hard_deleted": False},
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

                    logger.info(f"Bank account hard deleted with opening balance cleanup: {bank_account_id}, CoA: {coa_id}")

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
            # Verify bank account exists
            ba = await conn.fetchval(
                """
                SELECT id FROM bank_accounts
                WHERE id = $1 AND tenant_id = $2
            """,
                bank_account_id,
                ctx["tenant_id"],
            )

            if not ba:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Build query
            conditions = ["bank_account_id = $1"]
            params = [bank_account_id]
            param_idx = 2

            if transaction_type:
                conditions.append(f"transaction_type = ${param_idx}")
                params.append(transaction_type)
                param_idx += 1

            if date_from:
                conditions.append(f"transaction_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"transaction_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            if is_reconciled is not None:
                conditions.append(f"is_reconciled = ${param_idx}")
                params.append(is_reconciled)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count total
            count_query = f"SELECT COUNT(*) FROM bank_transactions WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get transactions
            query = f"""
                SELECT id, transaction_date, transaction_type, amount, running_balance,
                       description, payee_payer, reference_type, reference_number,
                       is_reconciled, created_at
                FROM bank_transactions
                WHERE {where_clause}
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
                SELECT id, account_name, opening_balance, current_balance
                FROM bank_accounts
                WHERE id = $1 AND tenant_id = $2
            """,
                bank_account_id,
                ctx["tenant_id"],
            )

            if not ba:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Get transaction stats
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as transaction_count,
                    COUNT(*) FILTER (WHERE is_reconciled = false) as unreconciled_count,
                    COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) as total_deposits,
                    COALESCE(SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END), 0) as total_withdrawals,
                    MAX(transaction_date) as last_transaction_date
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
                    "current_balance": ba["current_balance"] or 0,
                    "total_deposits": int(stats["total_deposits"] or 0),
                    "total_withdrawals": int(stats["total_withdrawals"] or 0),
                    "transaction_count": stats["transaction_count"] or 0,
                    "unreconciled_count": stats["unreconciled_count"] or 0,
                    "last_transaction_date": stats["last_transaction_date"].isoformat()
                    if stats["last_transaction_date"]
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
                # Get bank account
                ba = await conn.fetchrow(
                    """
                    SELECT ba.*, coa.id as coa_account_id
                    FROM bank_accounts ba
                    LEFT JOIN chart_of_accounts coa ON ba.coa_id = coa.id
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

                current_balance = ba["current_balance"] or 0
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
                    ) VALUES ($1, $2, $3, $4, $5, 'ADJUSTMENT', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.adjustment_date,
                    f"Bank Adjustment - {ba['account_name']}: {body.reason}",
                    bank_account_id,
                    str(trace_id),
                    float(abs(adjustment)),
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
                        float(adjustment),
                        f"Penyesuaian Saldo - {body.reason}",
                    )
                    # Cr. Opening Balance Equity (as adjustment source)
                    equity_id = await conn.fetchval(
                        """
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1 AND account_code = $2
                    """,
                        ctx["tenant_id"],
                        OPENING_BALANCE_EQUITY,
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
                        float(adjustment),
                        f"Koreksi Saldo - {body.reason}",
                    )
                else:
                    # Dr. Opening Balance Equity
                    equity_id = await conn.fetchval(
                        """
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1 AND account_code = $2
                    """,
                        ctx["tenant_id"],
                        OPENING_BALANCE_EQUITY,
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
                        float(abs(adjustment)),
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
                        float(abs(adjustment)),
                        f"Penyesuaian Saldo - {body.reason}",
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


@router.get("/{bank_account_id}/transactions")
async def get_bank_account_transactions(
    request: Request,
    bank_account_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get transaction history for a bank account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit
        async with pool.acquire() as conn:
            account = await conn.fetchrow(
                "SELECT id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                bank_account_id,
                ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Build query with optional date filters
            date_filter = ""
            params = [ctx["tenant_id"], bank_account_id]
            param_idx = 3

            if start_date:
                date_filter += f" AND date >= ${param_idx}"
                params.append(start_date)
                param_idx += 1
            if end_date:
                date_filter += f" AND date <= ${param_idx}"
                params.append(end_date)
                param_idx += 1

            params.extend([limit, offset])

            rows = await conn.fetch(
                f"""
                SELECT * FROM (
                    SELECT id::text, 'receive_payment' as type, payment_number as reference,
                        payment_date as date, total_amount as amount, 'Payment Received' as description
                    FROM receive_payments
                    WHERE tenant_id = $1 AND bank_account_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id::text, 'expense' as type, expense_number as reference,
                        expense_date as date, -total_amount as amount, COALESCE(notes, 'Expense') as description
                    FROM expenses
                    WHERE tenant_id = $1 AND paid_through_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id::text, 'transfer_out' as type, transfer_number as reference,
                        transfer_date as date, -amount, 'Transfer Out' as description
                    FROM bank_transfers
                    WHERE tenant_id = $1 AND from_bank_id::text = $2 AND status = 'posted'
                    UNION ALL
                    SELECT id::text, 'transfer_in' as type, transfer_number as reference,
                        transfer_date as date, amount, 'Transfer In' as description
                    FROM bank_transfers
                    WHERE tenant_id = $1 AND to_bank_id::text = $2 AND status = 'posted'
                ) t
                WHERE 1=1 {date_filter}
                ORDER BY date DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
                *params,
            )

            transactions = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "reference": row["reference"],
                    "date": row["date"].isoformat() if row["date"] else None,
                    "amount": row["amount"],
                    "description": row["description"],
                }
                for row in rows
            ]

            return {
                "transactions": transactions,
                "page": page,
                "limit": limit,
                "account_name": account["account_name"],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bank transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")


# =============================================================================
# BANK ACCOUNT STATEMENT
# =============================================================================


@router.get("/{bank_account_id}/statement")
async def get_bank_account_statement(
    request: Request,
    bank_account_id: str,
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
):
    """Get a statement-style report for a bank account."""
    # Parse date strings to date objects
    from datetime import datetime
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            account = await conn.fetchrow(
                "SELECT id, account_name, current_balance FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                bank_account_id,
                ctx["tenant_id"],
            )
            if not account:
                raise HTTPException(status_code=404, detail="Bank account not found")

            # Get opening balance (balance before start_date)
            opening = await conn.fetchval(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN type IN ('receive_payment', 'transfer_in') THEN amount
                        ELSE -amount
                    END
                ), 0) as opening
                FROM (
                    SELECT total_amount as amount, 'receive_payment' as type
                    FROM receive_payments
                    WHERE tenant_id = $1 AND bank_account_id::text = $2
                      AND payment_date < $3 AND status = 'posted'
                    UNION ALL
                    SELECT total_amount as amount, 'expense' as type
                    FROM expenses
                    WHERE tenant_id = $1 AND paid_through_id::text = $2
                      AND expense_date < $3 AND status = 'posted'
                    UNION ALL
                    SELECT amount, 'transfer_out' as type
                    FROM bank_transfers
                    WHERE tenant_id = $1 AND from_bank_id::text = $2
                      AND transfer_date < $3 AND status = 'posted'
                    UNION ALL
                    SELECT amount, 'transfer_in' as type
                    FROM bank_transfers
                    WHERE tenant_id = $1 AND to_bank_id::text = $2
                      AND transfer_date < $3 AND status = 'posted'
                ) t
            """,
                ctx["tenant_id"],
                bank_account_id,
                start_dt,
            )

            # Get transactions in period
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT id::text, 'receive_payment' as type, payment_number as reference,
                        payment_date as date, total_amount as amount, 'Payment Received' as description
                    FROM receive_payments
                    WHERE tenant_id = $1 AND bank_account_id::text = $2
                      AND payment_date BETWEEN $3 AND $4 AND status = 'posted'
                    UNION ALL
                    SELECT id::text, 'expense' as type, expense_number as reference,
                        expense_date as date, -total_amount as amount, COALESCE(notes, 'Expense') as description
                    FROM expenses
                    WHERE tenant_id = $1 AND paid_through_id::text = $2
                      AND expense_date BETWEEN $3 AND $4 AND status = 'posted'
                    UNION ALL
                    SELECT id::text, 'transfer_out' as type, transfer_number as reference,
                        transfer_date as date, -amount, 'Transfer Out' as description
                    FROM bank_transfers
                    WHERE tenant_id = $1 AND from_bank_id::text = $2
                      AND transfer_date BETWEEN $3 AND $4 AND status = 'posted'
                    UNION ALL
                    SELECT id::text, 'transfer_in' as type, transfer_number as reference,
                        transfer_date as date, amount, 'Transfer In' as description
                    FROM bank_transfers
                    WHERE tenant_id = $1 AND to_bank_id::text = $2
                      AND transfer_date BETWEEN $3 AND $4 AND status = 'posted'
                ) t ORDER BY date ASC, id ASC
            """,
                ctx["tenant_id"],
                bank_account_id,
                start_dt,
                end_dt,
            )

            transactions = []
            running_balance = opening or 0
            for row in rows:
                running_balance += row["amount"]
                transactions.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "reference": row["reference"],
                        "date": row["date"].isoformat() if row["date"] else None,
                        "amount": row["amount"],
                        "description": row["description"],
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
                "opening_balance": opening or 0,
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
