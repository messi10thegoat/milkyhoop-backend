"""
Vendors Router - Vendor/Supplier Master Data Management

CRUD endpoints for managing vendors with search and pagination.
Integrates with bills module for purchase invoice management.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.vendors import (
    CreateVendorRequest,
    UpdateVendorRequest,
    VendorResponse,
    VendorListResponse,
    VendorDetailResponse,
    VendorAutocompleteResponse,
    VendorBalanceResponse,
    VendorDuplicateCheckResponse,
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
# HEALTH CHECK (must be before /{vendor_id} to avoid route conflict)
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint for the vendors service."""
    return {"status": "ok", "service": "vendors"}


# =============================================================================
# AUTOCOMPLETE (for quick search in forms)
# =============================================================================
@router.get("/autocomplete", response_model=VendorAutocompleteResponse)
async def autocomplete_vendors(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Quick vendor search for autocomplete in forms.
    Returns minimal data for fast response.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT id, name, code, phone
                FROM vendors
                WHERE tenant_id = $1
                  AND is_active = true
                  AND (name ILIKE $2 OR code ILIKE $2 OR phone ILIKE $2)
                ORDER BY name ASC
                LIMIT $3
            """
            rows = await conn.fetch(query, ctx["tenant_id"], f"%{q}%", limit)

            items = [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "code": row["code"],
                    "phone": row["phone"],
                }
                for row in rows
            ]

            return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in vendor autocomplete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Autocomplete failed")


# =============================================================================
# DUPLICATE CHECK (for form validation)
# =============================================================================
@router.get("/check-duplicate", response_model=VendorDuplicateCheckResponse)
async def check_duplicate(
    request: Request,
    nama: Optional[str] = Query(None, description="Vendor name to check"),
    npwp: Optional[str] = Query(None, description="NPWP to check"),
    excludeId: Optional[str] = Query(
        None, description="Vendor ID to exclude (for edit mode)"
    ),
):
    """
    Check for potential duplicate vendors by name or NPWP.

    Used for form validation before creating/updating vendors.

    **Returns:**
    - `byName`: Vendors with similar names (case-insensitive, partial match)
    - `byNpwp`: Vendors with exact NPWP match
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        by_name = []
        by_npwp = []

        async with pool.acquire() as conn:
            # Check by name (partial match, case-insensitive)
            if nama and nama.strip():
                name_query = """
                    SELECT id, name, company_name, tax_id
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND name ILIKE $2
                """
                params = [ctx["tenant_id"], f"%{nama.strip()}%"]

                if excludeId:
                    name_query += " AND id != $3"
                    params.append(UUID(excludeId))

                name_query += " LIMIT 10"

                rows = await conn.fetch(name_query, *params)
                by_name = [
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "company": row["company_name"],
                        "npwp": row["tax_id"],
                    }
                    for row in rows
                ]

            # Check by NPWP (exact match after normalization)
            if npwp and npwp.strip():
                # Normalize NPWP: remove dots and dashes
                normalized_npwp = npwp.replace(".", "").replace("-", "").strip()

                npwp_query = """
                    SELECT id, name, company_name, tax_id
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND REPLACE(REPLACE(tax_id, '.', ''), '-', '') = $2
                """
                params = [ctx["tenant_id"], normalized_npwp]

                if excludeId:
                    npwp_query += " AND id != $3"
                    params.append(UUID(excludeId))

                npwp_query += " LIMIT 10"

                rows = await conn.fetch(npwp_query, *params)
                by_npwp = [
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "company": row["company_name"],
                        "npwp": row["tax_id"],
                    }
                    for row in rows
                ]

        return {"byName": by_name, "byNpwp": by_npwp}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking vendor duplicates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Duplicate check failed")


# =============================================================================
# LIST VENDORS
# =============================================================================
@router.get("", response_model=VendorListResponse)
async def list_vendors(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search name, code, or contact"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    sort_by: Literal["name", "code", "created_at", "updated_at"] = Query(
        "name", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort order"),
):
    """
    List vendors with search, filtering, and pagination.

    **Search:** Matches name, code, contact_person, or phone.
    **Filter:** Use is_active=true to show only active vendors.
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
                    f"(name ILIKE ${param_idx} OR code ILIKE ${param_idx} "
                    f"OR contact_person ILIKE ${param_idx} OR phone ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if is_active is not None:
                conditions.append(f"is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Validate sort field
            valid_sorts = {
                "name": "name",
                "code": "code",
                "created_at": "created_at",
                "updated_at": "updated_at",
            }
            sort_field = valid_sorts.get(sort_by, "name")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Get total count
            count_query = f"SELECT COUNT(*) FROM vendors WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, code, name, contact_person, phone, email,
                       payment_terms_days, is_active, created_at
                FROM vendors
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
                    "contact_person": row["contact_person"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "payment_terms_days": row["payment_terms_days"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing vendors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list vendors")


# =============================================================================
# GET VENDOR DETAIL
# =============================================================================
@router.get("/{vendor_id}", response_model=VendorDetailResponse)
async def get_vendor(request: Request, vendor_id: UUID):
    """Get detailed information for a single vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT id, code, name, contact_person, phone, email,
                       address, city, province, postal_code, tax_id,
                       payment_terms_days, credit_limit, notes,
                       is_active, created_at, updated_at
                FROM vendors
                WHERE id = $1 AND tenant_id = $2
            """
            row = await conn.fetchrow(query, vendor_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Vendor not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "contact_person": row["contact_person"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "address": row["address"],
                    "city": row["city"],
                    "province": row["province"],
                    "postal_code": row["postal_code"],
                    "tax_id": row["tax_id"],
                    "payment_terms_days": row["payment_terms_days"],
                    "credit_limit": row["credit_limit"],
                    "notes": row["notes"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get vendor")


# =============================================================================
# GET VENDOR BALANCE (AP Balance)
# =============================================================================
@router.get("/{vendor_id}/balance", response_model=VendorBalanceResponse)
async def get_vendor_balance(request: Request, vendor_id: UUID):
    """
    Get vendor's accounts payable balance.

    Returns the total outstanding balance from unpaid bills, plus summary stats.

    **Returns:**
    - `total_balance`: Outstanding amount (unpaid + partial bills)
    - `unpaid_bills`: Count of bills with no payment
    - `partial_bills`: Count of partially paid bills
    - `overdue_bills`: Count of overdue bills
    - `overdue_amount`: Total overdue amount
    - `total_billed`: Total amount billed (historical)
    - `total_paid`: Total amount paid (historical)
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            vendor = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Get AP balance from bills table
            # status_v2 values: draft, posted, paid, void
            # We want bills that are posted but not fully paid
            balance_query = """
                SELECT
                    COALESCE(SUM(
                        CASE WHEN status_v2 NOT IN ('draft', 'void', 'paid')
                        THEN COALESCE(grand_total, amount) - COALESCE(amount_paid, 0)
                        ELSE 0 END
                    ), 0) as total_balance,
                    COUNT(*) FILTER (
                        WHERE status_v2 = 'posted' AND COALESCE(amount_paid, 0) = 0
                    ) as unpaid_count,
                    COUNT(*) FILTER (
                        WHERE status_v2 = 'posted'
                        AND COALESCE(amount_paid, 0) > 0
                        AND COALESCE(amount_paid, 0) < COALESCE(grand_total, amount)
                    ) as partial_count,
                    COUNT(*) FILTER (
                        WHERE due_date < CURRENT_DATE
                        AND status_v2 NOT IN ('draft', 'void', 'paid')
                        AND COALESCE(amount_paid, 0) < COALESCE(grand_total, amount)
                    ) as overdue_count,
                    COALESCE(SUM(
                        CASE WHEN due_date < CURRENT_DATE
                        AND status_v2 NOT IN ('draft', 'void', 'paid')
                        THEN COALESCE(grand_total, amount) - COALESCE(amount_paid, 0)
                        ELSE 0 END
                    ), 0) as overdue_amount,
                    COALESCE(SUM(COALESCE(grand_total, amount)), 0) as total_billed,
                    COALESCE(SUM(COALESCE(amount_paid, 0)), 0) as total_paid
                FROM bills
                WHERE tenant_id = $1 AND vendor_id = $2 AND status_v2 != 'void'
            """
            balance = await conn.fetchrow(balance_query, ctx["tenant_id"], vendor_id)

            return {
                "success": True,
                "data": {
                    "vendor_id": str(vendor_id),
                    "vendor_name": vendor["name"],
                    "total_balance": int(balance["total_balance"] or 0),
                    "unpaid_bills": balance["unpaid_count"] or 0,
                    "partial_bills": balance["partial_count"] or 0,
                    "overdue_bills": balance["overdue_count"] or 0,
                    "overdue_amount": int(balance["overdue_amount"] or 0),
                    "total_billed": int(balance["total_billed"] or 0),
                    "total_paid": int(balance["total_paid"] or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor balance {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get vendor balance")


# =============================================================================
# CREATE VENDOR
# =============================================================================
@router.post("", response_model=VendorResponse, status_code=201)
async def create_vendor(request: Request, body: CreateVendorRequest):
    """
    Create a new vendor.

    **Constraints:**
    - Vendor name must be unique within tenant
    - Vendor code (if provided) must be unique within tenant
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check for duplicate name
            existing = await conn.fetchval(
                "SELECT id FROM vendors WHERE tenant_id = $1 AND name = $2",
                ctx["tenant_id"],
                body.name,
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Vendor with name '{body.name}' already exists",
                )

            # Check for duplicate code if provided
            if body.code:
                existing_code = await conn.fetchval(
                    "SELECT id FROM vendors WHERE tenant_id = $1 AND code = $2",
                    ctx["tenant_id"],
                    body.code,
                )
                if existing_code:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Vendor with code '{body.code}' already exists",
                    )

            # Insert vendor
            vendor_id = await conn.fetchval(
                """
                INSERT INTO vendors (
                    tenant_id, code, name, contact_person, phone, email,
                    address, city, province, postal_code, tax_id,
                    payment_terms_days, credit_limit, notes, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                RETURNING id
            """,
                ctx["tenant_id"],
                body.code,
                body.name,
                body.contact_person,
                body.phone,
                body.email,
                body.address,
                body.city,
                body.province,
                body.postal_code,
                body.tax_id,
                body.payment_terms_days,
                body.credit_limit,
                body.notes,
                ctx["user_id"],
            )

            logger.info(f"Vendor created: {vendor_id}, name={body.name}")

            return {
                "success": True,
                "message": "Vendor created successfully",
                "data": {"id": str(vendor_id), "name": body.name, "code": body.code},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating vendor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create vendor")


# =============================================================================
# UPDATE VENDOR
# =============================================================================
@router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(request: Request, vendor_id: UUID, body: UpdateVendorRequest):
    """
    Update an existing vendor.

    Only provided fields will be updated (partial update).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            existing = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Check for duplicate name if name is being changed
            if body.name and body.name != existing["name"]:
                duplicate = await conn.fetchval(
                    "SELECT id FROM vendors WHERE tenant_id = $1 AND name = $2 AND id != $3",
                    ctx["tenant_id"],
                    body.name,
                    vendor_id,
                )
                if duplicate:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Vendor with name '{body.name}' already exists",
                    )

            # Build update query dynamically
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": True,
                    "message": "No changes provided",
                    "data": {"id": str(vendor_id)},
                }

            updates = []
            params = []
            param_idx = 1

            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            updates.append("updated_at = NOW()")
            params.extend([vendor_id, ctx["tenant_id"]])

            query = f"""
                UPDATE vendors
                SET {', '.join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            logger.info(f"Vendor updated: {vendor_id}")

            return {
                "success": True,
                "message": "Vendor updated successfully",
                "data": {"id": str(vendor_id)},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update vendor")


# =============================================================================
# DELETE VENDOR (Soft delete by setting is_active = false)
# =============================================================================
@router.delete("/{vendor_id}", response_model=VendorResponse)
async def delete_vendor(request: Request, vendor_id: UUID):
    """
    Soft delete a vendor by setting is_active to false.

    **Note:** This is a soft delete. The vendor record is preserved but
    won't appear in autocomplete or active vendor lists.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            existing = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Soft delete
            await conn.execute(
                """
                UPDATE vendors
                SET is_active = false, updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                vendor_id,
                ctx["tenant_id"],
            )

            logger.info(f"Vendor soft deleted: {vendor_id}, name={existing['name']}")

            return {
                "success": True,
                "message": "Vendor deleted successfully",
                "data": {"id": str(vendor_id), "name": existing["name"]},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete vendor")
