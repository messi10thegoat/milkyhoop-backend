"""
Customers Router - Customer Master Data Management

CRUD endpoints for managing customers with search and pagination.
Integrates with AR module for balance tracking.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.customers import (
    CreateCustomerRequest,
    UpdateCustomerRequest,
    CustomerResponse,
    CustomerListResponse,
    CustomerDetailResponse,
    CustomerAutocompleteResponse,
    CustomerBalanceResponse,
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
# HEALTH CHECK (must be before /{customer_id} to avoid route conflict)
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint for the customers service."""
    return {"status": "ok", "service": "customers"}


# =============================================================================
# AUTOCOMPLETE (for quick search in forms)
# =============================================================================
@router.get("/autocomplete", response_model=CustomerAutocompleteResponse)
async def autocomplete_customers(
    request: Request,
    q: str = Query(default="", description="Search query"),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Quick customer search for autocomplete in forms.
    Returns minimal data for fast response.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT id, nama, nomor_member, telepon
                FROM customers
                WHERE tenant_id = $1
                  AND (nama ILIKE $2 OR nomor_member ILIKE $2 OR telepon ILIKE $2)
                ORDER BY nama ASC
                LIMIT $3
            """
            rows = await conn.fetch(query, ctx["tenant_id"], f"%{q}%", limit)

            items = [
                {
                    "id": str(row["id"]),
                    "name": row["nama"],
                    "code": row["nomor_member"],
                    "phone": row["telepon"],
                }
                for row in rows
            ]

            return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in customer autocomplete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Autocomplete failed")


# =============================================================================
# LIST CUSTOMERS
# =============================================================================
@router.get("", response_model=CustomerListResponse)
async def list_customers(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search name, code, or contact"),
    tipe: Optional[str] = Query(None, description="Filter by customer type"),
    sort_by: Literal["name", "code", "created_at", "updated_at"] = Query(
        "name", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort order"),
):
    """
    List customers with search, filtering, and pagination.

    **Search:** Matches nama, nomor_member, or telepon.
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
                    f"(nama ILIKE ${param_idx} OR nomor_member ILIKE ${param_idx} "
                    f"OR telepon ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if tipe is not None:
                conditions.append(f"tipe = ${param_idx}")
                params.append(tipe)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Validate sort field
            valid_sorts = {
                "name": "nama",
                "code": "nomor_member",
                "created_at": "created_at",
                "updated_at": "updated_at"
            }
            sort_field = valid_sorts.get(sort_by, "nama")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Get total count
            count_query = f"SELECT COUNT(*) FROM customers WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, nomor_member, nama, tipe, telepon, email, alamat,
                       points, total_transaksi, total_nilai, saldo_hutang, created_at
                FROM customers
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["nomor_member"],
                    "name": row["nama"],
                    "type": row["tipe"],
                    "phone": row["telepon"],
                    "email": row["email"],
                    "address": row["alamat"],
                    "points": row["points"],
                    "total_transactions": row["total_transaksi"],
                    "total_value": row["total_nilai"],
                    "outstanding_balance": row["saldo_hutang"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
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
        logger.error(f"Error listing customers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list customers")


# =============================================================================
# GET CUSTOMER DETAIL
# =============================================================================
@router.get("/{customer_id}", response_model=CustomerDetailResponse)
async def get_customer(request: Request, customer_id: str):
    """Get detailed information for a single customer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT id, nomor_member, nama, tipe, telepon, email, alamat,
                       points, points_per_50k, total_transaksi, total_nilai,
                       saldo_hutang, last_transaction_at, created_at, updated_at,
                       default_currency_id
                FROM customers
                WHERE id = $1 AND tenant_id = $2
            """
            row = await conn.fetchrow(query, customer_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Customer not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["nomor_member"],
                    "name": row["nama"],
                    "type": row["tipe"],
                    "phone": row["telepon"],
                    "email": row["email"],
                    "address": row["alamat"],
                    "points": row["points"],
                    "points_per_50k": row["points_per_50k"],
                    "total_transactions": row["total_transaksi"],
                    "total_value": row["total_nilai"],
                    "outstanding_balance": row["saldo_hutang"],
                    "last_transaction_at": row["last_transaction_at"].isoformat() if row["last_transaction_at"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "default_currency_id": str(row["default_currency_id"]) if row["default_currency_id"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get customer")


# =============================================================================
# GET CUSTOMER BALANCE (AR Balance)
# =============================================================================
@router.get("/{customer_id}/balance", response_model=CustomerBalanceResponse)
async def get_customer_balance(request: Request, customer_id: str):
    """
    Get customer's accounts receivable balance.

    Returns the total outstanding balance from unpaid invoices.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if customer exists
            customer = await conn.fetchrow(
                "SELECT id, nama, saldo_hutang FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id, ctx["tenant_id"]
            )
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")

            # Get AR balance from accounts_receivable table
            balance_query = """
                SELECT
                    COALESCE(SUM(balance), 0) as total_balance,
                    COUNT(*) FILTER (WHERE status = 'OPEN') as open_count,
                    COUNT(*) FILTER (WHERE status = 'PARTIAL') as partial_count,
                    COUNT(*) FILTER (WHERE due_date < CURRENT_DATE AND status IN ('OPEN', 'PARTIAL')) as overdue_count
                FROM accounts_receivable
                WHERE tenant_id = $1
                  AND customer_id = $2
                  AND status IN ('OPEN', 'PARTIAL')
            """
            balance = await conn.fetchrow(balance_query, ctx["tenant_id"], customer_id)

            return {
                "success": True,
                "data": {
                    "customer_id": str(customer_id),
                    "customer_name": customer["nama"],
                    "total_balance": int(balance["total_balance"] or customer["saldo_hutang"] or 0),
                    "open_invoices": balance["open_count"] or 0,
                    "partial_invoices": balance["partial_count"] or 0,
                    "overdue_invoices": balance["overdue_count"] or 0,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting customer balance {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get customer balance")


# =============================================================================
# CREATE CUSTOMER
# =============================================================================
@router.post("", response_model=CustomerResponse, status_code=201)
async def create_customer(request: Request, body: CreateCustomerRequest):
    """
    Create a new customer.

    **Constraints:**
    - Customer name must be unique within tenant
    - Customer code (if provided) must be unique within tenant
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check for duplicate name
            existing = await conn.fetchval(
                "SELECT id FROM customers WHERE tenant_id = $1 AND name = $2",
                ctx["tenant_id"], body.name
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer with name '{body.name}' already exists"
                )

            # Check for duplicate code if provided
            if body.code:
                existing_code = await conn.fetchval(
                    "SELECT id FROM customers WHERE tenant_id = $1 AND code = $2",
                    ctx["tenant_id"], body.code
                )
                if existing_code:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Customer with code '{body.code}' already exists"
                    )

            # Insert customer
            customer_id = await conn.fetchval("""
                INSERT INTO customers (
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
                ctx["user_id"]
            )

            logger.info(f"Customer created: {customer_id}, name={body.name}")

            return {
                "success": True,
                "message": "Customer created successfully",
                "data": {
                    "id": str(customer_id),
                    "name": body.name,
                    "code": body.code
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating customer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create customer")


# =============================================================================
# UPDATE CUSTOMER
# =============================================================================
@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer(request: Request, customer_id: UUID, body: UpdateCustomerRequest):
    """
    Update an existing customer.

    Only provided fields will be updated (partial update).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if customer exists
            existing = await conn.fetchrow(
                "SELECT id, name FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Customer not found")

            # Check for duplicate name if name is being changed
            if body.name and body.name != existing["name"]:
                duplicate = await conn.fetchval(
                    "SELECT id FROM customers WHERE tenant_id = $1 AND name = $2 AND id != $3",
                    ctx["tenant_id"], body.name, customer_id
                )
                if duplicate:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Customer with name '{body.name}' already exists"
                    )

            # Build update query dynamically
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": True,
                    "message": "No changes provided",
                    "data": {"id": str(customer_id)}
                }

            updates = []
            params = []
            param_idx = 1

            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            updates.append("updated_at = NOW()")
            params.extend([customer_id, ctx["tenant_id"]])

            query = f"""
                UPDATE customers
                SET {', '.join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            logger.info(f"Customer updated: {customer_id}")

            return {
                "success": True,
                "message": "Customer updated successfully",
                "data": {"id": str(customer_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update customer")


# =============================================================================
# DELETE CUSTOMER (Soft delete by setting is_active = false)
# =============================================================================
@router.delete("/{customer_id}", response_model=CustomerResponse)
async def delete_customer(request: Request, customer_id: UUID):
    """
    Soft delete a customer by setting is_active to false.

    **Note:** This is a soft delete. The customer record is preserved but
    won't appear in autocomplete or active customer lists.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if customer exists
            existing = await conn.fetchrow(
                "SELECT id, name FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Customer not found")

            # Check for outstanding AR balance
            balance = await conn.fetchval("""
                SELECT COALESCE(SUM(balance), 0)
                FROM accounts_receivable
                WHERE tenant_id = $1
                  AND customer_id = $2::text
                  AND status IN ('OPEN', 'PARTIAL')
            """, ctx["tenant_id"], str(customer_id))

            if balance and balance > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot delete customer with outstanding balance of Rp {balance:,}"
                )

            # Soft delete
            await conn.execute("""
                UPDATE customers
                SET is_active = false, updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """, customer_id, ctx["tenant_id"])

            logger.info(f"Customer soft deleted: {customer_id}, name={existing['name']}")

            return {
                "success": True,
                "message": "Customer deleted successfully",
                "data": {"id": str(customer_id), "name": existing["name"]}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete customer")
