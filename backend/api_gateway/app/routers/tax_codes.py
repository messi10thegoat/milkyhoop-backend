"""
Tax Codes Router - Tax Master Data Management

CRUD endpoints for managing tax codes (PPN, PPh, etc.) with search and pagination.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.tax_codes import (
    CreateTaxCodeRequest,
    UpdateTaxCodeRequest,
    TaxCodeResponse,
    TaxCodeListResponse,
    TaxCodeDetailResponse,
    TaxCodeDropdownResponse,
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


# =============================================================================
# HEALTH CHECK (must be before /{tax_code_id} to avoid route conflict)
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint for the tax codes service."""
    return {"status": "ok", "service": "tax-codes"}


# =============================================================================
# DROPDOWN (for select components in forms)
# =============================================================================
@router.get("/dropdown", response_model=TaxCodeDropdownResponse)
async def get_tax_dropdown(
    request: Request,
    tax_type: Optional[str] = Query(None, description="Filter by tax type (ppn, pph21, etc.)")
):
    """
    Get tax codes for dropdown/select components.
    Returns only active tax codes with minimal data.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1", "is_active = true"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if tax_type:
                conditions.append(f"tax_type = ${param_idx}")
                params.append(tax_type)

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT id, code, name, rate, is_default
                FROM tax_codes
                WHERE {where_clause}
                ORDER BY is_default DESC, rate ASC, name ASC
            """
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "rate": float(row["rate"]),
                    "is_default": row["is_default"],
                }
                for row in rows
            ]

            return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting tax dropdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get tax dropdown")


# =============================================================================
# SEED DEFAULT TAX CODES
# =============================================================================
@router.post("/seed", response_model=TaxCodeResponse)
async def seed_default_tax_codes(request: Request):
    """
    Seed default tax codes for the tenant.
    This creates PPN 11%, PPN 12%, PPN 0%, and No Tax if they don't exist.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT seed_default_tax_codes($1)", ctx["tenant_id"])

            logger.info(f"Seeded default tax codes for tenant {ctx['tenant_id']}")

            return {
                "success": True,
                "message": "Default tax codes seeded successfully",
                "data": {}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error seeding tax codes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to seed tax codes")


# =============================================================================
# LIST TAX CODES
# =============================================================================
@router.get("", response_model=TaxCodeListResponse)
async def list_tax_codes(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search code or name"),
    tax_type: Optional[str] = Query(None, description="Filter by tax type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: Literal["code", "name", "rate", "created_at"] = Query(
        "code", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort order"),
):
    """
    List tax codes with search, filtering, and pagination.
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
                    f"(code ILIKE ${param_idx} OR name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if tax_type:
                conditions.append(f"tax_type = ${param_idx}")
                params.append(tax_type)
                param_idx += 1

            if is_active is not None:
                conditions.append(f"is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Validate sort field
            valid_sorts = {
                "code": "code",
                "name": "name",
                "rate": "rate",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "code")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Get total count
            count_query = f"SELECT COUNT(*) FROM tax_codes WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, code, name, rate, tax_type, is_inclusive, is_active, is_default
                FROM tax_codes
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "rate": float(row["rate"]),
                    "tax_type": row["tax_type"],
                    "is_inclusive": row["is_inclusive"],
                    "is_active": row["is_active"],
                    "is_default": row["is_default"],
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing tax codes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list tax codes")


# =============================================================================
# GET TAX CODE DETAIL
# =============================================================================
@router.get("/{tax_code_id}", response_model=TaxCodeDetailResponse)
async def get_tax_code(request: Request, tax_code_id: UUID):
    """Get detailed information for a single tax code."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT id, code, name, rate, tax_type, is_inclusive,
                       sales_tax_account, purchase_tax_account, description,
                       is_active, is_default, created_at, updated_at
                FROM tax_codes
                WHERE id = $1 AND tenant_id = $2
            """
            row = await conn.fetchrow(query, tax_code_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Tax code not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "rate": float(row["rate"]),
                    "tax_type": row["tax_type"],
                    "is_inclusive": row["is_inclusive"],
                    "sales_tax_account": row["sales_tax_account"],
                    "purchase_tax_account": row["purchase_tax_account"],
                    "description": row["description"],
                    "is_active": row["is_active"],
                    "is_default": row["is_default"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting tax code {tax_code_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get tax code")


# =============================================================================
# CREATE TAX CODE
# =============================================================================
@router.post("", response_model=TaxCodeResponse, status_code=201)
async def create_tax_code(request: Request, body: CreateTaxCodeRequest):
    """
    Create a new tax code.

    **Constraints:**
    - Tax code must be unique within tenant
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check for duplicate code
            existing = await conn.fetchval(
                "SELECT id FROM tax_codes WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], body.code
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tax code '{body.code}' already exists"
                )

            # Insert tax code
            tax_code_id = await conn.fetchval("""
                INSERT INTO tax_codes (
                    tenant_id, code, name, rate, tax_type, is_inclusive,
                    sales_tax_account, purchase_tax_account, description,
                    is_default, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
            """,
                ctx["tenant_id"],
                body.code,
                body.name,
                body.rate,
                body.tax_type,
                body.is_inclusive,
                body.sales_tax_account,
                body.purchase_tax_account,
                body.description,
                body.is_default,
                ctx["user_id"]
            )

            logger.info(f"Tax code created: {tax_code_id}, code={body.code}")

            return {
                "success": True,
                "message": "Tax code created successfully",
                "data": {
                    "id": str(tax_code_id),
                    "code": body.code,
                    "name": body.name
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating tax code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create tax code")


# =============================================================================
# UPDATE TAX CODE
# =============================================================================
@router.patch("/{tax_code_id}", response_model=TaxCodeResponse)
async def update_tax_code(request: Request, tax_code_id: UUID, body: UpdateTaxCodeRequest):
    """
    Update an existing tax code.

    Only provided fields will be updated (partial update).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if tax code exists
            existing = await conn.fetchrow(
                "SELECT id, code FROM tax_codes WHERE id = $1 AND tenant_id = $2",
                tax_code_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Tax code not found")

            # Check for duplicate code if code is being changed
            if body.code and body.code != existing["code"]:
                duplicate = await conn.fetchval(
                    "SELECT id FROM tax_codes WHERE tenant_id = $1 AND code = $2 AND id != $3",
                    ctx["tenant_id"], body.code, tax_code_id
                )
                if duplicate:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Tax code '{body.code}' already exists"
                    )

            # Build update query dynamically
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": True,
                    "message": "No changes provided",
                    "data": {"id": str(tax_code_id)}
                }

            updates = []
            params = []
            param_idx = 1

            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            updates.append("updated_at = NOW()")
            params.extend([tax_code_id, ctx["tenant_id"]])

            query = f"""
                UPDATE tax_codes
                SET {', '.join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            logger.info(f"Tax code updated: {tax_code_id}")

            return {
                "success": True,
                "message": "Tax code updated successfully",
                "data": {"id": str(tax_code_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating tax code {tax_code_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update tax code")


# =============================================================================
# DELETE TAX CODE (Soft delete)
# =============================================================================
@router.delete("/{tax_code_id}", response_model=TaxCodeResponse)
async def delete_tax_code(request: Request, tax_code_id: UUID):
    """
    Soft delete a tax code by setting is_active to false.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if tax code exists
            existing = await conn.fetchrow(
                "SELECT id, code FROM tax_codes WHERE id = $1 AND tenant_id = $2",
                tax_code_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Tax code not found")

            # Soft delete
            await conn.execute("""
                UPDATE tax_codes
                SET is_active = false, is_default = false, updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """, tax_code_id, ctx["tenant_id"])

            logger.info(f"Tax code soft deleted: {tax_code_id}, code={existing['code']}")

            return {
                "success": True,
                "message": "Tax code deleted successfully",
                "data": {"id": str(tax_code_id), "code": existing["code"]}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting tax code {tax_code_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete tax code")
