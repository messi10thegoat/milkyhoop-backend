"""
Chart of Accounts Router - CoA Master Data Management

CRUD endpoints for managing Chart of Accounts with hierarchy support.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.accounts import (
    CreateAccountRequest,
    UpdateAccountRequest,
    AccountResponse,
    AccountListResponse,
    AccountTreeResponse,
    AccountDetailResponse,
    AccountDropdownResponse,
    AccountBalanceResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (initialized on first request)
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


def build_tree(accounts: list, parent_code=None) -> list:
    """Build hierarchical tree from flat list of accounts."""
    tree = []
    for account in accounts:
        if account.get("parent_code") == parent_code:
            children = build_tree(accounts, account["account_code"])
            item = {
                "id": account["id"],
                "code": account["account_code"],
                "name": account["name"],
                "type": account["account_type"],
                "normal_balance": account["normal_balance"],
                "is_active": account["is_active"],
                "is_header": account.get("is_header", False),
                "children": children
            }
            tree.append(item)
    return tree


# =============================================================================
# HEALTH CHECK (must be before /{account_id} to avoid route conflict)
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint for the accounts service."""
    return {"status": "ok", "service": "accounts"}


# =============================================================================
# DROPDOWN (for select components in forms)
# =============================================================================
@router.get("/dropdown", response_model=AccountDropdownResponse)
async def get_account_dropdown(
    request: Request,
    type: Optional[str] = Query(None, description="Filter by account type (ASSET, LIABILITY, etc.)"),
    search: Optional[str] = Query(None, description="Search code or name")
):
    """
    Get accounts for dropdown/select components.
    Returns only active accounts with minimal data.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1", "is_active = true"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if type:
                conditions.append(f"account_type = ${param_idx}")
                params.append(type)
                param_idx += 1

            if search:
                conditions.append(f"(account_code ILIKE ${param_idx} OR name ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT id, account_code, name, account_type
                FROM chart_of_accounts
                WHERE {where_clause}
                ORDER BY account_code ASC
                LIMIT 100
            """
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["account_code"],
                    "name": row["name"],
                    "type": row["account_type"],
                    "full_name": f"{row['account_code']} - {row['name']}",
                }
                for row in rows
            ]

            return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting account dropdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account dropdown")


# =============================================================================
# GET ACCOUNTS TREE (hierarchical view)
# =============================================================================
@router.get("/tree", response_model=AccountTreeResponse)
async def get_accounts_tree(
    request: Request,
    type: Optional[str] = Query(None, description="Filter by account type"),
    include_inactive: bool = Query(False, description="Include inactive accounts")
):
    """
    Get accounts in hierarchical tree structure.
    Useful for displaying CoA in tree view.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if not include_inactive:
                conditions.append("is_active = true")

            if type:
                conditions.append(f"account_type = ${param_idx}")
                params.append(type)

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT id, account_code, name, account_type, normal_balance, parent_code,
                       is_active, is_header
                FROM chart_of_accounts
                WHERE {where_clause}
                ORDER BY account_code ASC
            """
            rows = await conn.fetch(query, *params)

            # Convert to list of dicts
            accounts = [
                {
                    "id": str(row["id"]),
                    "account_code": row["account_code"],
                    "name": row["name"],
                    "account_type": row["account_type"],
                    "normal_balance": row["normal_balance"],
                    "parent_code": row["parent_code"],
                    "is_active": row["is_active"],
                    "is_header": row["is_header"],
                }
                for row in rows
            ]

            # Build tree
            tree = build_tree(accounts)

            return {"items": tree}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting accounts tree: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get accounts tree")


# =============================================================================
# LIST ACCOUNTS (flat view)
# =============================================================================
@router.get("", response_model=AccountListResponse)
async def list_accounts(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(100, ge=1, le=500, description="Items per page"),
    search: Optional[str] = Query(None, description="Search code or name"),
    type: Optional[str] = Query(None, description="Filter by account type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: Literal["code", "name", "type", "created_at"] = Query(
        "code", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort order"),
):
    """
    List accounts in flat format with search, filtering, and pagination.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(
                    f"(account_code ILIKE ${param_idx} OR name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if type:
                conditions.append(f"account_type = ${param_idx}")
                params.append(type)
                param_idx += 1

            if is_active is not None:
                conditions.append(f"is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Validate sort field
            valid_sorts = {
                "code": "account_code",
                "name": "name",
                "type": "account_type",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "account_code")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Get total count
            count_query = f"SELECT COUNT(*) FROM chart_of_accounts WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items - simplified query without recursive CTE for better compatibility
            query = f"""
                SELECT id, account_code, name, account_type, normal_balance, parent_code,
                       is_active, is_header, level
                FROM chart_of_accounts
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["account_code"],
                    "name": row["name"],
                    "type": row["account_type"],
                    "normal_balance": row["normal_balance"],
                    "parent_code": row["parent_code"],
                    "is_active": row["is_active"],
                    "is_header": row["is_header"],
                    "level": row["level"] or 0,
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list accounts")


# =============================================================================
# GET ACCOUNT DETAIL
# =============================================================================
@router.get("/{account_id}", response_model=AccountDetailResponse)
async def get_account(request: Request, account_id: UUID):
    """Get detailed information for a single account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT c.id, c.account_code, c.name, c.account_type, c.normal_balance,
                       c.parent_code, p.name as parent_name,
                       c.is_active, c.is_header, c.description, c.category,
                       c.created_at, c.updated_at
                FROM chart_of_accounts c
                LEFT JOIN chart_of_accounts p ON c.parent_code = p.account_code AND c.tenant_id = p.tenant_id
                WHERE c.id = $1 AND c.tenant_id = $2
            """
            row = await conn.fetchrow(query, account_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["account_code"],
                    "name": row["name"],
                    "type": row["account_type"],
                    "normal_balance": row["normal_balance"],
                    "parent_code": row["parent_code"],
                    "parent_name": row["parent_name"],
                    "is_active": row["is_active"],
                    "is_header": row["is_header"],
                    "description": row["description"],
                    "category": row["category"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account")


# =============================================================================
# GET ACCOUNT BALANCE
# =============================================================================
@router.get("/{account_id}/balance", response_model=AccountBalanceResponse)
async def get_account_balance(
    request: Request,
    account_id: UUID,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    """
    Get account balance from journal lines.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get account info
            account = await conn.fetchrow(
                "SELECT id, account_code, name, account_type, normal_balance FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                account_id, ctx["tenant_id"]
            )
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Build date conditions
            date_conditions = []
            params = [ctx["tenant_id"], account_id]
            param_idx = 3

            if start_date:
                date_conditions.append(f"je.entry_date >= ${param_idx}::date")
                params.append(start_date)
                param_idx += 1

            if end_date:
                date_conditions.append(f"je.entry_date <= ${param_idx}::date")
                params.append(end_date)

            date_clause = " AND " + " AND ".join(date_conditions) if date_conditions else ""

            # Get balance from journal lines
            balance_query = f"""
                SELECT
                    COALESCE(SUM(jl.debit), 0) as debit_total,
                    COALESCE(SUM(jl.credit), 0) as credit_total
                FROM journal_lines jl
                INNER JOIN journal_entries je ON jl.journal_id = je.id
                WHERE je.tenant_id = $1
                  AND jl.account_id = $2
                  AND je.status = 'POSTED'
                  {date_clause}
            """
            balance = await conn.fetchrow(balance_query, *params)

            debit_total = int(balance["debit_total"] or 0)
            credit_total = int(balance["credit_total"] or 0)

            # Calculate net balance based on normal balance
            if account["normal_balance"] == "DEBIT":
                net_balance = debit_total - credit_total
            else:
                net_balance = credit_total - debit_total

            return {
                "success": True,
                "data": {
                    "id": str(account["id"]),
                    "code": account["account_code"],
                    "name": account["name"],
                    "type": account["account_type"],
                    "normal_balance": account["normal_balance"],
                    "debit_total": debit_total,
                    "credit_total": credit_total,
                    "balance": net_balance,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting account balance {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account balance")


# =============================================================================
# CREATE ACCOUNT
# =============================================================================
@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(request: Request, body: CreateAccountRequest):
    """
    Create a new account.

    **Constraints:**
    - Account code must be unique within tenant
    - Parent account must exist if specified
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check for duplicate code
            existing = await conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                ctx["tenant_id"], body.code
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Account with code '{body.code}' already exists"
                )

            # Validate parent if specified
            parent_code = None
            if body.parent_id:
                # parent_id can be UUID or account_code
                parent = await conn.fetchrow(
                    "SELECT id, account_code, account_type FROM chart_of_accounts WHERE (id::text = $1 OR account_code = $1) AND tenant_id = $2",
                    body.parent_id, ctx["tenant_id"]
                )
                if not parent:
                    raise HTTPException(status_code=400, detail="Parent account not found")

                parent_code = parent["account_code"]

                # Parent and child should have same type
                if parent["account_type"] != body.type:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Child account type must match parent type ({parent['account_type']})"
                    )

            # Determine level based on parent
            level = 1
            if parent_code:
                parent_level = await conn.fetchval(
                    "SELECT level FROM chart_of_accounts WHERE account_code = $1 AND tenant_id = $2",
                    parent_code, ctx["tenant_id"]
                )
                level = (parent_level or 1) + 1

            # Insert account
            account_id = await conn.fetchval("""
                INSERT INTO chart_of_accounts (
                    tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_active
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, true)
                RETURNING id
            """,
                ctx["tenant_id"],
                body.code,
                body.name,
                body.type,
                body.normal_balance,
                parent_code,
                level
            )

            logger.info(f"Account created: {account_id}, code={body.code}")

            return {
                "success": True,
                "message": "Account created successfully",
                "data": {
                    "id": str(account_id),
                    "code": body.code,
                    "name": body.name
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create account")


# =============================================================================
# UPDATE ACCOUNT
# =============================================================================
@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(request: Request, account_id: UUID, body: UpdateAccountRequest):
    """
    Update an existing account.

    **Constraints:**
    - System accounts cannot have their type changed
    - Only code, name, parent_id, is_active, and metadata can be updated
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if account exists
            existing = await conn.fetchrow(
                "SELECT id, code, is_system FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                account_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Account not found")

            # Check for duplicate code if code is being changed
            if body.code and body.code != existing["code"]:
                duplicate = await conn.fetchval(
                    "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND code = $2 AND id != $3",
                    ctx["tenant_id"], body.code, account_id
                )
                if duplicate:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Account with code '{body.code}' already exists"
                    )

            # Build update query dynamically
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": True,
                    "message": "No changes provided",
                    "data": {"id": str(account_id)}
                }

            # Handle parent_id conversion
            if "parent_id" in update_data:
                if update_data["parent_id"]:
                    try:
                        update_data["parent_id"] = UUID(update_data["parent_id"])
                    except ValueError:
                        raise HTTPException(status_code=400, detail="Invalid parent_id format")
                else:
                    update_data["parent_id"] = None

            # Handle metadata conversion
            if "metadata" in update_data and update_data["metadata"] is not None:
                import json
                update_data["metadata"] = json.dumps(update_data["metadata"])

            updates = []
            params = []
            param_idx = 1

            for field, value in update_data.items():
                if field == "metadata":
                    updates.append(f"{field} = ${param_idx}::jsonb")
                else:
                    updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            updates.append("updated_at = NOW()")
            params.extend([account_id, ctx["tenant_id"]])

            query = f"""
                UPDATE chart_of_accounts
                SET {', '.join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            logger.info(f"Account updated: {account_id}")

            return {
                "success": True,
                "message": "Account updated successfully",
                "data": {"id": str(account_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update account")


# =============================================================================
# DELETE ACCOUNT (Soft delete)
# =============================================================================
@router.delete("/{account_id}", response_model=AccountResponse)
async def delete_account(request: Request, account_id: UUID):
    """
    Soft delete an account by setting is_active to false.

    **Constraints:**
    - System accounts cannot be deleted
    - Accounts with journal entries cannot be deleted
    - Accounts with children cannot be deleted
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if account exists
            existing = await conn.fetchrow(
                "SELECT id, code, is_system FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                account_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Account not found")

            # Check if system account
            if existing["is_system"]:
                raise HTTPException(
                    status_code=400,
                    detail="System accounts cannot be deleted"
                )

            # Check for children
            has_children = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM chart_of_accounts WHERE parent_id = $1)",
                account_id
            )
            if has_children:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete account with child accounts"
                )

            # Check for journal entries
            has_transactions = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM journal_lines WHERE account_id = $1 LIMIT 1)",
                account_id
            )
            if has_transactions:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete account with existing transactions. Deactivate instead."
                )

            # Soft delete
            await conn.execute("""
                UPDATE chart_of_accounts
                SET is_active = false, updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """, account_id, ctx["tenant_id"])

            logger.info(f"Account soft deleted: {account_id}, code={existing['code']}")

            return {
                "success": True,
                "message": "Account deleted successfully",
                "data": {"id": str(account_id), "code": existing["code"]}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete account")
