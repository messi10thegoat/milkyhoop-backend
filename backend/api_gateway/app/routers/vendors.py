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
                  AND (name ILIKE $2 OR code ILIKE $2 OR company_name ILIKE $2 OR display_name ILIKE $2 OR phone ILIKE $2)
                ORDER BY name ASC
                LIMIT $3
            """
            rows = await conn.fetch(query, ctx["tenant_id"], f"%{q}%", limit)

            items = [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
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
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
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
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
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
    is_active: bool = Query(True, description="Filter by active status"),
    sort_by: Literal["name", "code", "created_at", "updated_at"] = Query(
        "created_at", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
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
                    f"OR company_name ILIKE ${param_idx} OR display_name ILIKE ${param_idx} "
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
                SELECT id, code, name, company_name, display_name, contact_person, phone, email,
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
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
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
            # Query only columns that exist in the vendors table
            query = """
                SELECT id, code, name, company_name, display_name, contact_person, phone, email,
                       address, city, province, postal_code, tax_id,
                       payment_terms_days, credit_limit, notes,
                       vendor_type, nik, is_pkp,
                       default_tax_code, default_pph_type, default_pph_rate,
                       company_name, display_name, mobile_phone, website,
                       currency, opening_balance, opening_balance_date,
                       bank_name, bank_account_number, bank_account_holder,
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
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
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
                    # Extended fields from actual table
                    "vendor_type": row["vendor_type"],
                    "nik": row["nik"],
                    "is_pkp": row["is_pkp"],
                    "default_tax_code": row["default_tax_code"],
                    "default_pph_type": row["default_pph_type"],
                    "default_pph_rate": float(row["default_pph_rate"])
                    if row["default_pph_rate"]
                    else None,
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
                    "mobile_phone": row["mobile_phone"],
                    "website": row["website"],
                    "currency": row["currency"],
                    "opening_balance": row["opening_balance"],
                    "opening_balance_date": row["opening_balance_date"].isoformat()
                    if row["opening_balance_date"]
                    else None,
                    # Bank details
                    "bank_name": row["bank_name"],
                    "bank_account_number": row["bank_account_number"],
                    "bank_account_holder": row["bank_account_holder"],
                    # Status and timestamps
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
                        CASE WHEN status_v2 NOT IN (draft, void, paid)
                        THEN COALESCE(grand_total, amount) - COALESCE(amount_paid, 0)
                        ELSE 0 END
                    ), 0) as total_balance,
                    COUNT(*) FILTER (
                        WHERE status_v2 = posted AND COALESCE(amount_paid, 0) = 0
                    ) as unpaid_count,
                    COUNT(*) FILTER (
                        WHERE status_v2 = posted
                        AND COALESCE(amount_paid, 0) > 0
                        AND COALESCE(amount_paid, 0) < COALESCE(grand_total, amount)
                    ) as partial_count,
                    COUNT(*) FILTER (
                        WHERE due_date < CURRENT_DATE
                        AND status_v2 NOT IN (draft, void, paid)
                        AND COALESCE(amount_paid, 0) < COALESCE(grand_total, amount)
                    ) as overdue_count,
                    COALESCE(SUM(
                        CASE WHEN due_date < CURRENT_DATE
                        AND status_v2 NOT IN (draft, void, paid)
                        THEN COALESCE(grand_total, amount) - COALESCE(amount_paid, 0)
                        ELSE 0 END
                    ), 0) as overdue_amount,
                    COALESCE(SUM(COALESCE(grand_total, amount)), 0) as total_billed,
                    COALESCE(SUM(COALESCE(amount_paid, 0)), 0) as total_paid
                FROM bills
                WHERE tenant_id = $1 AND vendor_id = $2 AND status_v2 != void
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

    **Extended fields (QB/Xero/Zoho aligned):**
    - company_name, display_name: Legal and display names
    - mobile_phone, website: Additional contact info
    - is_pkp, currency: Tax and currency settings
    - bank_name, bank_account_number, bank_account_holder: Payment details
    """
    from datetime import datetime, date

    def parse_date(date_str):
        """Parse date string to date object."""
        if not date_str:
            return None
        if isinstance(date_str, date):
            return date_str
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

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

            # Parse opening_balance_date from string to date
            opening_date = parse_date(getattr(body, "opening_balance_date", None))

            # Insert vendor with ALL fields including extended columns
            vendor_id = await conn.fetchval(
                """
                INSERT INTO vendors (
                    tenant_id, code, name, contact_person, phone, email,
                    address, city, province, postal_code, tax_id,
                    payment_terms_days, credit_limit, notes,
                    vendor_type, opening_balance, opening_balance_date,
                    company_name, display_name, mobile_phone, website,
                    is_pkp, currency,
                    bank_name, bank_account_number, bank_account_holder,
                    created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27
                )
                RETURNING id
                """,
                ctx["tenant_id"],
                body.code,
                body.name,
                getattr(body, "contact_person", None),
                body.phone,
                body.email,
                getattr(body, "address", None),
                getattr(body, "city", None),
                getattr(body, "province", None),
                getattr(body, "postal_code", None),
                getattr(body, "tax_id", None),
                getattr(body, "payment_terms_days", 30),
                getattr(body, "credit_limit", None),
                getattr(body, "notes", None),
                getattr(body, "vendor_type", "BADAN"),
                getattr(body, "opening_balance", 0),
                opening_date,
                getattr(body, "company_name", None),
                getattr(body, "display_name", None),
                getattr(body, "mobile_phone", None),
                getattr(body, "website", None),
                getattr(body, "is_pkp", False),
                getattr(body, "currency", "IDR"),
                getattr(body, "bank_name", None),
                getattr(body, "bank_account_number", None),
                getattr(body, "bank_account_holder", None),
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
# TOGGLE VENDOR STATUS
# =============================================================================
@router.patch("/{vendor_id}/status", response_model=VendorResponse)
async def toggle_vendor_status(
    request: Request,
    vendor_id: UUID,
    status: Literal["active", "inactive"] = Query(..., description="New status"),
):
    """
    Toggle vendor active/inactive status.

    This is a convenience endpoint for quickly changing vendor status.
    Equivalent to PATCH /vendors/:id with { is_active: true/false }
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            existing = await conn.fetchrow(
                "SELECT id, name, is_active FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Vendor not found")

            is_active = status == "active"

            # Update status
            await conn.execute(
                """
                UPDATE vendors
                SET is_active = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3
                """,
                is_active,
                vendor_id,
                ctx["tenant_id"],
            )

            logger.info(f"Vendor status changed: {vendor_id}, status={status}")

            return {
                "success": True,
                "message": "Status vendor berhasil diubah",
                "data": {
                    "id": str(vendor_id),
                    "name": existing["name"],
                    "is_active": is_active,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling vendor status {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update vendor status")


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


# =============================================================================
# VENDOR NEXT CODE (for form auto-generation)
# =============================================================================


@router.get("/next-code")
async def get_next_vendor_code(request: Request):
    """Get the next available vendor code for auto-generation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT code FROM vendors WHERE tenant_id = $1 AND code IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                ctx["tenant_id"],
            )
            import re

            if row and row["code"]:
                match = re.match(r"^([A-Z]*)([0-9]+)$", row["code"])
                if match:
                    prefix = match.group(1) or "V"
                    num = int(match.group(2)) + 1
                    next_code = f"{prefix}{num:04d}"
                else:
                    next_code = "V0001"
            else:
                next_code = "V0001"
            return {"success": True, "next_code": next_code}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting next vendor code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get next code")


# =============================================================================
# VENDOR TRANSACTIONS HISTORY
# =============================================================================


@router.get("/{vendor_id}/transactions")
async def get_vendor_transactions(
    request: Request,
    vendor_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get transaction history for a vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit
        async with pool.acquire() as conn:
            vendor = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")
            # Fixed: use correct column names from bills and purchase_orders tables
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT id::text, 'bill' as type, invoice_number as number,
                        issue_date as date, COALESCE(grand_total, amount) as amount, 'Bill' as description, status_v2 as status
                    FROM bills WHERE tenant_id = $1 AND vendor_id::text = $2
                    UNION ALL
                    SELECT id::text, 'purchase_order' as type, po_number as number,
                        po_date as date, total_amount as amount, 'Purchase Order' as description, status
                    FROM purchase_orders WHERE tenant_id = $1 AND vendor_id::text = $2
                ) t ORDER BY date DESC LIMIT $3 OFFSET $4
            """,
                ctx["tenant_id"],
                vendor_id,
                limit,
                offset,
            )
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT id FROM bills WHERE tenant_id = $1 AND vendor_id::text = $2
                    UNION ALL SELECT id FROM purchase_orders WHERE tenant_id = $1 AND vendor_id::text = $2
                ) t
            """,
                ctx["tenant_id"],
                vendor_id,
            )
            transactions = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "number": row["number"],
                    "date": row["date"].isoformat() if row["date"] else None,
                    "amount": row["amount"],
                    "description": row["description"],
                    "status": row["status"],
                }
                for row in rows
            ]
            return {
                "transactions": transactions,
                "total": count,
                "page": page,
                "limit": limit,
                "has_more": offset + limit < count,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")


# =============================================================================
# VENDOR OPEN BILLS (for pay bills feature)
# =============================================================================


@router.get("/{vendor_id}/open-bills")
async def get_vendor_open_bills(request: Request, vendor_id: str):
    """Get open (unpaid/partially paid) bills for a vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Fixed: use correct column names from bills table
            # invoice_number instead of bill_number
            # issue_date instead of bill_date
            # grand_total/amount instead of total_amount
            # status_v2 instead of status
            rows = await conn.fetch(
                """
                SELECT
                    id, invoice_number, issue_date, due_date,
                    COALESCE(grand_total, amount) as total_amount, amount_paid,
                    COALESCE(grand_total, amount) - COALESCE(amount_paid, 0) as remaining_amount,
                    CASE WHEN due_date < CURRENT_DATE THEN true ELSE false END as is_overdue
                FROM bills
                WHERE tenant_id = $1
                  AND vendor_id::text = $2
                  AND status_v2 = 'posted'
                  AND COALESCE(grand_total, amount) > COALESCE(amount_paid, 0)
                ORDER BY due_date ASC
            """,
                ctx["tenant_id"],
                vendor_id,
            )
            bills = [
                {
                    "id": str(row["id"]),
                    "bill_number": row["invoice_number"],
                    "bill_date": row["issue_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "paid_amount": row["amount_paid"] or 0,
                    "remaining_amount": row["remaining_amount"],
                    "is_overdue": row["is_overdue"],
                }
                for row in rows
            ]
            total_outstanding = sum(b["remaining_amount"] for b in bills)
            return {"bills": bills, "total_outstanding": total_outstanding}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting open bills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get open bills")


# =============================================================================
# VENDOR OPENING BALANCE
# =============================================================================


@router.post("/{vendor_id}/opening-balance")
async def set_vendor_opening_balance(request: Request, vendor_id: str):
    """Set or update vendor opening AP balance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        amount = body.get("amount", 0)
        async with pool.acquire() as conn:
            vendor = await conn.fetchrow(
                "SELECT id FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")
            # Fixed: use opening_balance instead of balance (which doesn't exist)
            await conn.execute(
                "UPDATE vendors SET opening_balance = $1, updated_at = NOW() WHERE id = $2 AND tenant_id = $3",
                amount,
                vendor_id,
                ctx["tenant_id"],
            )
            return {
                "success": True,
                "message": "Opening balance updated",
                "data": {"vendor_id": vendor_id, "amount": amount},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting opening balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set opening balance")
