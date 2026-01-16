"""
Branches Router - Multi-Branch Management

Manages branches within the same tenant with separate accounting,
permissions, and branch transfers.

Journal Entries (Branch Transfer):
- From Branch: Dr. Branch Receivable / Cr. Inventory
- To Branch: Dr. Inventory / Cr. Branch Payable
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
from datetime import date
import logging
import asyncpg

from ..schemas.branches import (
    CreateBranchRequest,
    UpdateBranchRequest,
    BranchListResponse,
    BranchDetailResponse,
    BranchTreeResponse,
    CreateBranchPermissionRequest,
    UpdateBranchPermissionRequest,
    BranchPermissionListResponse,
    UserBranchesResponse,
    CreateBranchTransferRequest,
    BranchTransferListResponse,
    BranchTransferDetailResponse,
    BranchSummaryResponse,
    BranchTrialBalanceResponse,
    BranchComparisonResponse,
    BranchRankingResponse,
    BranchResponse,
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
    return {"status": "ok", "service": "branches"}


# =============================================================================
# BRANCHES
# =============================================================================
@router.get("", response_model=BranchListResponse)
async def list_branches(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    branch_level: Optional[int] = Query(None, ge=1, le=5),
    sort_by: Literal["name", "code", "created_at"] = Query("name"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
):
    """List branches with pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["b.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(f"(b.name ILIKE ${param_idx} OR b.code ILIKE ${param_idx} OR b.city ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            if is_active is not None:
                conditions.append(f"b.is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            if branch_level:
                conditions.append(f"b.branch_level = ${param_idx}")
                params.append(branch_level)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            sort_column = {"name": "b.name", "code": "b.code", "created_at": "b.created_at"}[sort_by]

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM branches b WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT b.*, pb.name as parent_branch_name,
                       (SELECT COUNT(*) FROM journal_entries je WHERE je.branch_id = b.id) as tx_count
                FROM branches b
                LEFT JOIN branches pb ON pb.id = b.parent_branch_id
                WHERE {where_clause}
                ORDER BY {sort_column} {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "city": row["city"],
                    "branch_level": row["branch_level"],
                    "is_headquarters": row["is_headquarters"],
                    "is_active": row["is_active"],
                    "parent_branch_id": str(row["parent_branch_id"]) if row["parent_branch_id"] else None,
                    "parent_branch_name": row["parent_branch_name"],
                    "transaction_count": row["tx_count"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing branches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list branches")


@router.post("", response_model=BranchResponse, status_code=201)
async def create_branch(request: Request, body: CreateBranchRequest):
    """Create a new branch."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check duplicate code
            exists = await conn.fetchval(
                "SELECT 1 FROM branches WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], body.code
            )
            if exists:
                raise HTTPException(status_code=400, detail=f"Branch with code '{body.code}' already exists")

            # If is_headquarters, ensure no other HQ exists
            if body.is_headquarters:
                hq_exists = await conn.fetchval(
                    "SELECT 1 FROM branches WHERE tenant_id = $1 AND is_headquarters = true",
                    ctx["tenant_id"]
                )
                if hq_exists:
                    raise HTTPException(status_code=400, detail="A headquarters branch already exists")

            # Insert branch
            branch_id = await conn.fetchval(
                """
                INSERT INTO branches (
                    tenant_id, code, name, address, city, province, postal_code,
                    country, phone, email, parent_branch_id, branch_level,
                    is_headquarters, has_own_sequence, default_warehouse_id,
                    default_bank_account_id, profit_center_id, opened_date, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                RETURNING id
                """,
                ctx["tenant_id"], body.code, body.name, body.address, body.city,
                body.province, body.postal_code, body.country, body.phone, body.email,
                body.parent_branch_id, body.branch_level, body.is_headquarters,
                body.has_own_sequence, body.default_warehouse_id,
                body.default_bank_account_id, body.profit_center_id,
                body.opened_date, ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Branch created successfully",
                "data": {"id": str(branch_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating branch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create branch")


@router.get("/tree", response_model=BranchTreeResponse)
async def get_branch_tree(request: Request):
    """Get branch hierarchy as tree structure."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM branches
                WHERE tenant_id = $1 AND is_active = true
                ORDER BY branch_level, name
                """,
                ctx["tenant_id"]
            )

            # Build tree
            branches_by_id = {}
            root_branches = []

            for row in rows:
                branch = {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "branch_level": row["branch_level"],
                    "is_headquarters": row["is_headquarters"],
                    "is_active": row["is_active"],
                    "children": [],
                }
                branches_by_id[str(row["id"])] = branch

                if row["parent_branch_id"]:
                    parent_id = str(row["parent_branch_id"])
                    if parent_id in branches_by_id:
                        branches_by_id[parent_id]["children"].append(branch)
                else:
                    root_branches.append(branch)

            return {"success": True, "data": root_branches}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting branch tree: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get branch tree")


@router.get("/{branch_id}", response_model=BranchDetailResponse)
async def get_branch(request: Request, branch_id: UUID):
    """Get branch detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT b.*,
                       pb.name as parent_branch_name,
                       w.name as warehouse_name,
                       ba.account_name as bank_account_name,
                       cc.name as cost_center_name
                FROM branches b
                LEFT JOIN branches pb ON pb.id = b.parent_branch_id
                LEFT JOIN warehouses w ON w.id = b.default_warehouse_id
                LEFT JOIN bank_accounts ba ON ba.id = b.default_bank_account_id
                LEFT JOIN cost_centers cc ON cc.id = b.profit_center_id
                WHERE b.tenant_id = $1 AND b.id = $2
            """
            row = await conn.fetchrow(query, ctx["tenant_id"], branch_id)
            if not row:
                raise HTTPException(status_code=404, detail="Branch not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "address": row["address"],
                    "city": row["city"],
                    "province": row["province"],
                    "postal_code": row["postal_code"],
                    "country": row["country"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "parent_branch_id": str(row["parent_branch_id"]) if row["parent_branch_id"] else None,
                    "parent_branch_name": row["parent_branch_name"],
                    "branch_level": row["branch_level"],
                    "is_headquarters": row["is_headquarters"],
                    "has_own_sequence": row["has_own_sequence"],
                    "default_warehouse_id": str(row["default_warehouse_id"]) if row["default_warehouse_id"] else None,
                    "default_warehouse_name": row["warehouse_name"],
                    "default_bank_account_id": str(row["default_bank_account_id"]) if row["default_bank_account_id"] else None,
                    "default_bank_account_name": row["bank_account_name"],
                    "profit_center_id": str(row["profit_center_id"]) if row["profit_center_id"] else None,
                    "profit_center_name": row["cost_center_name"],
                    "is_active": row["is_active"],
                    "opened_date": row["opened_date"],
                    "closed_date": row["closed_date"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting branch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get branch")


@router.patch("/{branch_id}", response_model=BranchResponse)
async def update_branch(request: Request, branch_id: UUID, body: UpdateBranchRequest):
    """Update branch."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM branches WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], branch_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Branch not found")

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
            params.extend([ctx["tenant_id"], branch_id])

            query = f"""
                UPDATE branches SET {', '.join(updates)}
                WHERE tenant_id = ${param_idx} AND id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            return {"success": True, "message": "Branch updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating branch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update branch")


@router.delete("/{branch_id}", response_model=BranchResponse)
async def close_branch(request: Request, branch_id: UUID):
    """Close/deactivate branch."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE branches
                SET is_active = false, closed_date = CURRENT_DATE, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                """,
                ctx["tenant_id"], branch_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Branch not found")

            return {"success": True, "message": "Branch closed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing branch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close branch")


# =============================================================================
# PERMISSIONS
# =============================================================================
@router.get("/{branch_id}/permissions", response_model=BranchPermissionListResponse)
async def list_branch_permissions(request: Request, branch_id: UUID):
    """List permissions for a branch."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify branch
            branch = await conn.fetchrow(
                "SELECT name FROM branches WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], branch_id
            )
            if not branch:
                raise HTTPException(status_code=404, detail="Branch not found")

            rows = await conn.fetch(
                """
                SELECT bp.*
                FROM branch_permissions bp
                WHERE bp.tenant_id = $1 AND bp.branch_id = $2
                ORDER BY bp.created_at
                """,
                ctx["tenant_id"], branch_id
            )

            items = [
                {
                    "id": str(row["id"]),
                    "user_id": str(row["user_id"]),
                    "user_name": None,  # Would need user table join
                    "branch_id": str(row["branch_id"]),
                    "branch_name": branch["name"],
                    "can_view": row["can_view"],
                    "can_create": row["can_create"],
                    "can_edit": row["can_edit"],
                    "can_delete": row["can_delete"],
                    "can_approve": row["can_approve"],
                    "is_default": row["is_default"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            return {"success": True, "items": items, "total": len(items)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing branch permissions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list permissions")


@router.post("/{branch_id}/permissions", response_model=BranchResponse, status_code=201)
async def grant_permission(request: Request, branch_id: UUID, body: CreateBranchPermissionRequest):
    """Grant branch permission to user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify branch
            exists = await conn.fetchval(
                "SELECT 1 FROM branches WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], branch_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Branch not found")

            # If setting as default, clear other defaults for user
            if body.is_default:
                await conn.execute(
                    "UPDATE branch_permissions SET is_default = false WHERE tenant_id = $1 AND user_id = $2",
                    ctx["tenant_id"], body.user_id
                )

            # Upsert permission
            perm_id = await conn.fetchval(
                """
                INSERT INTO branch_permissions (
                    tenant_id, user_id, branch_id, can_view, can_create,
                    can_edit, can_delete, can_approve, is_default, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (tenant_id, user_id, branch_id)
                DO UPDATE SET
                    can_view = $4, can_create = $5, can_edit = $6,
                    can_delete = $7, can_approve = $8, is_default = $9
                RETURNING id
                """,
                ctx["tenant_id"], body.user_id, branch_id, body.can_view,
                body.can_create, body.can_edit, body.can_delete, body.can_approve,
                body.is_default, ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Permission granted",
                "data": {"id": str(perm_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error granting permission: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to grant permission")


@router.delete("/permissions/{permission_id}", response_model=BranchResponse)
async def revoke_permission(request: Request, permission_id: UUID):
    """Revoke branch permission."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM branch_permissions WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], permission_id
            )
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Permission not found")

            return {"success": True, "message": "Permission revoked"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking permission: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to revoke permission")


# =============================================================================
# USER BRANCHES
# =============================================================================
@router.get("/users/{user_id}/branches", response_model=UserBranchesResponse)
async def get_user_branches(request: Request, user_id: UUID):
    """Get branches accessible by user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT bp.*, b.code as branch_code, b.name as branch_name
                FROM branch_permissions bp
                JOIN branches b ON b.id = bp.branch_id
                WHERE bp.tenant_id = $1 AND bp.user_id = $2 AND b.is_active = true
                ORDER BY bp.is_default DESC, b.name
                """,
                ctx["tenant_id"], user_id
            )

            items = [
                {
                    "branch_id": str(row["branch_id"]),
                    "branch_code": row["branch_code"],
                    "branch_name": row["branch_name"],
                    "can_view": row["can_view"],
                    "can_create": row["can_create"],
                    "can_edit": row["can_edit"],
                    "can_delete": row["can_delete"],
                    "can_approve": row["can_approve"],
                    "is_default": row["is_default"],
                }
                for row in rows
            ]

            return {"success": True, "items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user branches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get user branches")


# =============================================================================
# BRANCH TRANSFERS
# =============================================================================
@router.get("/transfers", response_model=BranchTransferListResponse)
async def list_branch_transfers(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    from_branch_id: Optional[UUID] = Query(None),
    to_branch_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List branch transfers."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["bt.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if from_branch_id:
                conditions.append(f"bt.from_branch_id = ${param_idx}")
                params.append(from_branch_id)
                param_idx += 1

            if to_branch_id:
                conditions.append(f"bt.to_branch_id = ${param_idx}")
                params.append(to_branch_id)
                param_idx += 1

            if status:
                conditions.append(f"bt.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if start_date:
                conditions.append(f"bt.transfer_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"bt.transfer_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM branch_transfers bt WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT bt.*,
                       fb.name as from_branch_name,
                       tb.name as to_branch_name,
                       (SELECT COUNT(*) FROM branch_transfer_lines WHERE branch_transfer_id = bt.id) as item_count
                FROM branch_transfers bt
                JOIN branches fb ON fb.id = bt.from_branch_id
                JOIN branches tb ON tb.id = bt.to_branch_id
                WHERE {where_clause}
                ORDER BY bt.transfer_date {sort_order}, bt.created_at {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "transfer_number": row["transfer_number"],
                    "transfer_date": row["transfer_date"],
                    "from_branch_id": str(row["from_branch_id"]),
                    "from_branch_name": row["from_branch_name"],
                    "to_branch_id": str(row["to_branch_id"]),
                    "to_branch_name": row["to_branch_name"],
                    "transfer_price": row["transfer_price"],
                    "status": row["status"],
                    "item_count": row["item_count"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing branch transfers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list branch transfers")


@router.post("/transfers", response_model=BranchResponse, status_code=201)
async def create_branch_transfer(request: Request, body: CreateBranchTransferRequest):
    """Create branch transfer with journal entries."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate transfer number
                tx_number = await conn.fetchval(
                    "SELECT generate_branch_transfer_number($1)",
                    ctx["tenant_id"]
                )

                # Calculate total
                total_price = sum(
                    int(line.quantity * line.unit_cost) for line in body.lines
                )

                # Apply markup if specified
                if body.pricing_method == "markup" and body.markup_percent:
                    total_price = int(total_price * (1 + float(body.markup_percent) / 100))

                # Create transfer
                transfer_id = await conn.fetchval(
                    """
                    INSERT INTO branch_transfers (
                        tenant_id, transfer_number, transfer_date, from_branch_id,
                        to_branch_id, transfer_price, pricing_method, markup_percent,
                        notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    ctx["tenant_id"], tx_number, body.transfer_date, body.from_branch_id,
                    body.to_branch_id, total_price, body.pricing_method, body.markup_percent,
                    body.notes, ctx["user_id"]
                )

                # Create lines
                for line in body.lines:
                    line_total = int(line.quantity * line.unit_cost)
                    await conn.execute(
                        """
                        INSERT INTO branch_transfer_lines (
                            branch_transfer_id, product_id, quantity, unit,
                            unit_cost, line_total, batch_id, serial_ids, notes
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        transfer_id, line.product_id, line.quantity, line.unit,
                        line.unit_cost, line_total, line.batch_id,
                        line.serial_ids, line.notes
                    )

                # Create journal entry for from branch
                # Dr. Branch Receivable (1-10950) / Cr. Inventory (1-10400)
                from_journal_id = await create_from_branch_journal(
                    conn, ctx["tenant_id"], transfer_id, tx_number,
                    body, total_price, ctx["user_id"]
                )

                if from_journal_id:
                    await conn.execute(
                        "UPDATE branch_transfers SET from_journal_id = $1 WHERE id = $2",
                        from_journal_id, transfer_id
                    )

                return {
                    "success": True,
                    "message": "Branch transfer created",
                    "data": {
                        "id": str(transfer_id),
                        "transfer_number": tx_number,
                        "from_journal_id": str(from_journal_id) if from_journal_id else None
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating branch transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create branch transfer")


async def create_from_branch_journal(conn, tenant_id, transfer_id, tx_number, body, amount, user_id):
    """Create journal entry for sending branch."""
    try:
        branch_receivable = await conn.fetchrow(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND code = '1-10950'",
            tenant_id
        )
        inventory = await conn.fetchrow(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND code = '1-10400'",
            tenant_id
        )

        if not branch_receivable or not inventory:
            logger.warning(f"Branch accounts not found for tenant {tenant_id}")
            return None

        journal_number = f"JE-BT-{tx_number}"
        journal_id = await conn.fetchval(
            """
            INSERT INTO journal_entries (
                tenant_id, journal_number, entry_date, description,
                source_type, source_id, branch_id, status, created_by
            ) VALUES ($1, $2, $3, $4, 'branch_transfer', $5, $6, 'posted', $7)
            RETURNING id
            """,
            tenant_id, journal_number, body.transfer_date,
            f"Branch Transfer: {tx_number}",
            transfer_id, body.from_branch_id, user_id
        )

        # Dr. Branch Receivable
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, $3, 0, $4)
            """,
            journal_id, branch_receivable["id"], amount,
            f"Receivable from branch transfer {tx_number}"
        )

        # Cr. Inventory
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, 0, $3, $4)
            """,
            journal_id, inventory["id"], amount,
            f"Inventory transfer out {tx_number}"
        )

        return journal_id

    except Exception as e:
        logger.error(f"Error creating from branch journal: {e}")
        return None


@router.get("/transfers/{transfer_id}", response_model=BranchTransferDetailResponse)
async def get_branch_transfer(request: Request, transfer_id: UUID):
    """Get branch transfer detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT bt.*,
                       fb.name as from_branch_name,
                       tb.name as to_branch_name
                FROM branch_transfers bt
                JOIN branches fb ON fb.id = bt.from_branch_id
                JOIN branches tb ON tb.id = bt.to_branch_id
                WHERE bt.tenant_id = $1 AND bt.id = $2
            """
            row = await conn.fetchrow(query, ctx["tenant_id"], transfer_id)
            if not row:
                raise HTTPException(status_code=404, detail="Branch transfer not found")

            # Get lines
            lines = await conn.fetch(
                """
                SELECT btl.*, p.name as product_name, p.sku as product_sku,
                       ib.batch_number
                FROM branch_transfer_lines btl
                JOIN products p ON p.id = btl.product_id
                LEFT JOIN item_batches ib ON ib.id = btl.batch_id
                WHERE btl.branch_transfer_id = $1
                """,
                transfer_id
            )

            line_details = [
                {
                    "id": str(line["id"]),
                    "product_id": str(line["product_id"]),
                    "product_name": line["product_name"],
                    "product_sku": line["product_sku"],
                    "quantity": line["quantity"],
                    "unit": line["unit"],
                    "unit_cost": line["unit_cost"],
                    "line_total": line["line_total"],
                    "batch_id": str(line["batch_id"]) if line["batch_id"] else None,
                    "batch_number": line["batch_number"],
                }
                for line in lines
            ]

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "transfer_number": row["transfer_number"],
                    "transfer_date": row["transfer_date"],
                    "from_branch_id": str(row["from_branch_id"]),
                    "from_branch_name": row["from_branch_name"],
                    "to_branch_id": str(row["to_branch_id"]),
                    "to_branch_name": row["to_branch_name"],
                    "stock_transfer_id": str(row["stock_transfer_id"]) if row["stock_transfer_id"] else None,
                    "transfer_price": row["transfer_price"],
                    "pricing_method": row["pricing_method"],
                    "markup_percent": row["markup_percent"],
                    "status": row["status"],
                    "settlement_date": row["settlement_date"],
                    "settlement_journal_id": str(row["settlement_journal_id"]) if row["settlement_journal_id"] else None,
                    "from_journal_id": str(row["from_journal_id"]) if row["from_journal_id"] else None,
                    "to_journal_id": str(row["to_journal_id"]) if row["to_journal_id"] else None,
                    "notes": row["notes"],
                    "lines": line_details,
                    "created_at": row["created_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting branch transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get branch transfer")


@router.post("/transfers/{transfer_id}/ship", response_model=BranchResponse)
async def ship_transfer(request: Request, transfer_id: UUID):
    """Mark transfer as shipped/in transit."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE branch_transfers
                SET status = 'in_transit', updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status = 'pending'
                """,
                ctx["tenant_id"], transfer_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Transfer not found or not in pending status")

            return {"success": True, "message": "Transfer shipped"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error shipping transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to ship transfer")


@router.post("/transfers/{transfer_id}/receive", response_model=BranchResponse)
async def receive_transfer(request: Request, transfer_id: UUID):
    """Receive transfer at destination branch."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get transfer
                transfer = await conn.fetchrow(
                    """
                    SELECT * FROM branch_transfers
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    ctx["tenant_id"], transfer_id
                )
                if not transfer:
                    raise HTTPException(status_code=404, detail="Transfer not found")

                if transfer["status"] not in ("pending", "in_transit"):
                    raise HTTPException(status_code=400, detail="Transfer cannot be received in current status")

                # Update status
                await conn.execute(
                    """
                    UPDATE branch_transfers
                    SET status = 'received', updated_at = NOW()
                    WHERE id = $1
                    """,
                    transfer_id
                )

                # Create journal entry for receiving branch
                # Dr. Inventory (1-10400) / Cr. Branch Payable (2-10950)
                to_journal_id = await create_to_branch_journal(
                    conn, ctx["tenant_id"], transfer, ctx["user_id"]
                )

                if to_journal_id:
                    await conn.execute(
                        "UPDATE branch_transfers SET to_journal_id = $1 WHERE id = $2",
                        to_journal_id, transfer_id
                    )

                return {
                    "success": True,
                    "message": "Transfer received",
                    "data": {"to_journal_id": str(to_journal_id) if to_journal_id else None}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error receiving transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to receive transfer")


async def create_to_branch_journal(conn, tenant_id, transfer, user_id):
    """Create journal entry for receiving branch."""
    try:
        inventory = await conn.fetchrow(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND code = '1-10400'",
            tenant_id
        )
        branch_payable = await conn.fetchrow(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND code = '2-10950'",
            tenant_id
        )

        if not inventory or not branch_payable:
            return None

        journal_number = f"JE-BT-{transfer['transfer_number']}-RCV"
        journal_id = await conn.fetchval(
            """
            INSERT INTO journal_entries (
                tenant_id, journal_number, entry_date, description,
                source_type, source_id, branch_id, status, created_by
            ) VALUES ($1, $2, $3, $4, 'branch_transfer', $5, $6, 'posted', $7)
            RETURNING id
            """,
            tenant_id, journal_number, date.today(),
            f"Branch Transfer Receipt: {transfer['transfer_number']}",
            transfer["id"], transfer["to_branch_id"], user_id
        )

        # Dr. Inventory
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, $3, 0, $4)
            """,
            journal_id, inventory["id"], transfer["transfer_price"],
            f"Inventory transfer in {transfer['transfer_number']}"
        )

        # Cr. Branch Payable
        await conn.execute(
            """
            INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
            VALUES ($1, $2, 0, $3, $4)
            """,
            journal_id, branch_payable["id"], transfer["transfer_price"],
            f"Payable for branch transfer {transfer['transfer_number']}"
        )

        return journal_id

    except Exception as e:
        logger.error(f"Error creating to branch journal: {e}")
        return None


@router.post("/transfers/{transfer_id}/settle", response_model=BranchResponse)
async def settle_transfer(request: Request, transfer_id: UUID):
    """Financial settlement of branch transfer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE branch_transfers
                SET status = 'settled', settlement_date = CURRENT_DATE, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status = 'received'
                """,
                ctx["tenant_id"], transfer_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Transfer not found or not in received status")

            return {"success": True, "message": "Transfer settled"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error settling transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to settle transfer")


# =============================================================================
# REPORTS
# =============================================================================
@router.get("/{branch_id}/summary", response_model=BranchSummaryResponse)
async def get_branch_summary(
    request: Request,
    branch_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Get branch P&L summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            branch = await conn.fetchrow(
                "SELECT name FROM branches WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], branch_id
            )
            if not branch:
                raise HTTPException(status_code=404, detail="Branch not found")

            # Get revenue and expenses from journal entries
            summary = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN coa.account_type = 'revenue' THEN jl.credit - jl.debit ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN coa.account_type = 'expense' THEN jl.debit - jl.credit ELSE 0 END), 0) as expenses,
                    COUNT(DISTINCT je.id) as tx_count
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.branch_id = $2
                  AND je.entry_date BETWEEN $3 AND $4
                  AND je.status = 'posted'
                """,
                ctx["tenant_id"], branch_id, start_date, end_date
            )

            return {
                "success": True,
                "data": {
                    "branch_id": str(branch_id),
                    "branch_name": branch["name"],
                    "total_revenue": summary["revenue"] or 0,
                    "total_expenses": summary["expenses"] or 0,
                    "net_income": (summary["revenue"] or 0) - (summary["expenses"] or 0),
                    "transaction_count": summary["tx_count"] or 0,
                    "period_start": start_date,
                    "period_end": end_date,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting branch summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get branch summary")


@router.get("/{branch_id}/trial-balance", response_model=BranchTrialBalanceResponse)
async def get_branch_trial_balance(
    request: Request,
    branch_id: UUID,
    as_of_date: date = Query(...),
):
    """Get branch trial balance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            branch = await conn.fetchrow(
                "SELECT name FROM branches WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], branch_id
            )
            if not branch:
                raise HTTPException(status_code=404, detail="Branch not found")

            rows = await conn.fetch(
                """
                SELECT
                    coa.code as account_code,
                    coa.name as account_name,
                    COALESCE(SUM(jl.debit), 0) as debit,
                    COALESCE(SUM(jl.credit), 0) as credit
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.branch_id = $2
                    AND je.entry_date <= $3
                    AND je.status = 'posted'
                WHERE coa.tenant_id = $1
                GROUP BY coa.code, coa.name
                HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0
                ORDER BY coa.code
                """,
                ctx["tenant_id"], branch_id, as_of_date
            )

            tb_rows = [
                {
                    "account_code": row["account_code"],
                    "account_name": row["account_name"],
                    "debit": row["debit"],
                    "credit": row["credit"],
                    "balance": row["debit"] - row["credit"],
                }
                for row in rows
            ]

            total_debit = sum(r["debit"] for r in tb_rows)
            total_credit = sum(r["credit"] for r in tb_rows)

            return {
                "success": True,
                "branch_name": branch["name"],
                "as_of_date": as_of_date,
                "rows": tb_rows,
                "total_debit": total_debit,
                "total_credit": total_credit,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting branch trial balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get trial balance")


@router.get("/comparison", response_model=BranchComparisonResponse)
async def compare_branches(
    request: Request,
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Compare all branches performance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    b.id as branch_id,
                    b.name as branch_name,
                    COALESCE(SUM(CASE WHEN coa.account_type = 'revenue' THEN jl.credit - jl.debit ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN coa.account_type = 'expense' THEN jl.debit - jl.credit ELSE 0 END), 0) as expenses
                FROM branches b
                LEFT JOIN journal_entries je ON je.branch_id = b.id
                    AND je.entry_date BETWEEN $2 AND $3
                    AND je.status = 'posted'
                LEFT JOIN journal_lines jl ON jl.journal_id = je.id
                LEFT JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE b.tenant_id = $1 AND b.is_active = true
                GROUP BY b.id, b.name
                ORDER BY (COALESCE(SUM(CASE WHEN coa.account_type = 'revenue' THEN jl.credit - jl.debit ELSE 0 END), 0) -
                         COALESCE(SUM(CASE WHEN coa.account_type = 'expense' THEN jl.debit - jl.credit ELSE 0 END), 0)) DESC
                """,
                ctx["tenant_id"], start_date, end_date
            )

            items = []
            totals = {"revenue": 0, "expenses": 0, "net_income": 0}

            for row in rows:
                revenue = row["revenue"] or 0
                expenses = row["expenses"] or 0
                net_income = revenue - expenses
                margin = round((net_income / revenue * 100) if revenue else 0, 2)

                items.append({
                    "branch_id": str(row["branch_id"]),
                    "branch_name": row["branch_name"],
                    "revenue": revenue,
                    "expenses": expenses,
                    "net_income": net_income,
                    "margin_percent": margin,
                })

                totals["revenue"] += revenue
                totals["expenses"] += expenses
                totals["net_income"] += net_income

            return {
                "success": True,
                "period_start": start_date,
                "period_end": end_date,
                "items": items,
                "totals": totals,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing branches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to compare branches")


@router.get("/ranking", response_model=BranchRankingResponse)
async def rank_branches(
    request: Request,
    ranking_by: Literal["revenue", "profit", "transactions"] = Query("revenue"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Rank branches by metric."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            if ranking_by == "transactions":
                rows = await conn.fetch(
                    """
                    SELECT
                        b.id as branch_id,
                        b.name as branch_name,
                        COUNT(DISTINCT je.id) as value
                    FROM branches b
                    LEFT JOIN journal_entries je ON je.branch_id = b.id
                        AND je.entry_date BETWEEN $2 AND $3
                        AND je.status = 'posted'
                    WHERE b.tenant_id = $1 AND b.is_active = true
                    GROUP BY b.id, b.name
                    ORDER BY value DESC
                    """,
                    ctx["tenant_id"], start_date, end_date
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        b.id as branch_id,
                        b.name as branch_name,
                        CASE
                            WHEN $4 = 'revenue' THEN
                                COALESCE(SUM(CASE WHEN coa.account_type = 'revenue' THEN jl.credit - jl.debit ELSE 0 END), 0)
                            ELSE
                                COALESCE(SUM(CASE WHEN coa.account_type = 'revenue' THEN jl.credit - jl.debit ELSE 0 END), 0) -
                                COALESCE(SUM(CASE WHEN coa.account_type = 'expense' THEN jl.debit - jl.credit ELSE 0 END), 0)
                        END as value
                    FROM branches b
                    LEFT JOIN journal_entries je ON je.branch_id = b.id
                        AND je.entry_date BETWEEN $2 AND $3
                        AND je.status = 'posted'
                    LEFT JOIN journal_lines jl ON jl.journal_id = je.id
                    LEFT JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE b.tenant_id = $1 AND b.is_active = true
                    GROUP BY b.id, b.name
                    ORDER BY value DESC
                    """,
                    ctx["tenant_id"], start_date, end_date, ranking_by
                )

            total = sum(row["value"] or 0 for row in rows)
            items = []

            for rank, row in enumerate(rows, 1):
                value = row["value"] or 0
                items.append({
                    "rank": rank,
                    "branch_id": str(row["branch_id"]),
                    "branch_name": row["branch_name"],
                    "value": value,
                    "percent_of_total": round((value / total * 100) if total else 0, 2),
                })

            return {
                "success": True,
                "ranking_by": ranking_by,
                "period_start": start_date,
                "period_end": end_date,
                "items": items,
                "total": total,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ranking branches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to rank branches")
