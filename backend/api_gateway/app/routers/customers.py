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
    limit: int = Query(10, ge=1, le=50),
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
        "created_at", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
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
                "updated_at": "updated_at",
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
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

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
                       default_currency_id, is_active,
                       contact_person, city, province, postal_code,
                       tax_id, payment_terms_days, credit_limit, notes,
                       mobile_phone, website, company_name, display_name,
                       customer_type, is_pkp, nik, currency,
                       ar_opening_balance, opening_balance_date, opening_balance_notes
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
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
                    
                    # Contact
                    "contact_person": row["contact_person"],
                    "phone": row["telepon"],
                    "mobile_phone": row["mobile_phone"],
                    "email": row["email"],
                    "website": row["website"],
                    
                    # Address
                    "address": row["alamat"],
                    "city": row["city"],
                    "province": row["province"],
                    "postal_code": row["postal_code"],
                    
                    # Tax info
                    "tax_id": row["tax_id"],
                    "nik": row["nik"],
                    "is_pkp": row["is_pkp"] or False,
                    "customer_type": row["customer_type"],
                    
                    # Financial
                    "currency": row["currency"] or "IDR",
                    "payment_terms_days": row["payment_terms_days"] or 0,
                    "credit_limit": row["credit_limit"],
                    
                    # Opening balance
                    "ar_opening_balance": row["ar_opening_balance"] or 0,
                    "opening_balance_date": row["opening_balance_date"].isoformat()
                    if row["opening_balance_date"]
                    else None,
                    "opening_balance_notes": row["opening_balance_notes"],
                    
                    # Statistics
                    "points": row["points"],
                    "points_per_50k": row["points_per_50k"],
                    "total_transactions": row["total_transaksi"],
                    "total_value": row["total_nilai"],
                    "outstanding_balance": row["saldo_hutang"],
                    "last_transaction_at": row["last_transaction_at"].isoformat()
                    if row["last_transaction_at"]
                    else None,
                    
                    # Metadata
                    "default_currency_id": str(row["default_currency_id"])
                    if row["default_currency_id"]
                    else None,
                    "is_active": row["is_active"],
                    "notes": row["notes"],
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                    "updated_at": row["updated_at"].isoformat()
                    if row["updated_at"]
                    else None,
                },
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
                customer_id,
                ctx["tenant_id"],
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
                    "total_balance": int(
                        balance["total_balance"] or customer["saldo_hutang"] or 0
                    ),
                    "open_invoices": balance["open_count"] or 0,
                    "partial_invoices": balance["partial_count"] or 0,
                    "overdue_invoices": balance["overdue_count"] or 0,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting customer balance {customer_id}: {e}", exc_info=True
        )
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
                "SELECT id FROM customers WHERE tenant_id = $1 AND nama = $2",
                ctx["tenant_id"],
                body.name,
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Customer with name '{body.name}' already exists",
                )

            # Check for duplicate code if provided
            if body.code:
                existing_code = await conn.fetchval(
                    "SELECT id FROM customers WHERE tenant_id = $1 AND nomor_member = $2",
                    ctx["tenant_id"],
                    body.code,
                )
                if existing_code:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Customer with code '{body.code}' already exists",
                    )

            # Generate UUID for customer ID
            import uuid as uuid_mod

            new_id = str(uuid_mod.uuid4())
            
            # Parse opening_balance_date if provided
            opening_date = None
            if body.opening_balance_date:
                try:
                    from datetime import datetime
                    opening_date = datetime.strptime(body.opening_balance_date, "%Y-%m-%d").date()
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid opening_balance_date format. Use YYYY-MM-DD"
                    )

            # Insert customer with ALL fields
            await conn.execute(
                """
                INSERT INTO customers (
                    id, tenant_id, nomor_member, nama, telepon, email, alamat,
                    contact_person, city, province, postal_code,
                    tax_id, payment_terms_days, credit_limit, notes,
                    mobile_phone, website, company_name, display_name,
                    customer_type, is_pkp, nik, currency,
                    ar_opening_balance, opening_balance_date, opening_balance_notes,
                    created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10, $11,
                    $12, $13, $14, $15,
                    $16, $17, $18, $19,
                    $20, $21, $22, $23,
                    $24, $25, $26,
                    $27
                )
            """,
                new_id,                           # $1  id
                ctx["tenant_id"],                 # $2  tenant_id
                body.code,                        # $3  nomor_member
                body.name,                        # $4  nama
                body.phone,                       # $5  telepon
                body.email,                       # $6  email
                body.address,                     # $7  alamat
                body.contact_person,              # $8  contact_person
                body.city,                        # $9  city
                body.province,                    # $10 province
                body.postal_code,                 # $11 postal_code
                body.tax_id,                      # $12 tax_id
                body.payment_terms_days,          # $13 payment_terms_days
                body.credit_limit,                # $14 credit_limit
                body.notes,                       # $15 notes
                body.mobile_phone,                # $16 mobile_phone
                body.website,                     # $17 website
                body.company_name,                # $18 company_name
                body.display_name or body.name,   # $19 display_name (default to name)
                body.customer_type or "BADAN",    # $20 customer_type
                body.is_pkp,                      # $21 is_pkp
                body.nik,                         # $22 nik
                body.currency or "IDR",           # $23 currency
                body.ar_opening_balance or 0,    # $24 ar_opening_balance
                opening_date,                     # $25 opening_balance_date
                body.opening_balance_notes,       # $26 opening_balance_notes
                ctx["user_id"],                   # $27 created_by
            )

            logger.info(f"Customer created: {new_id}, name={body.name}")

            return {
                "success": True,
                "message": "Customer created successfully",
                "data": {"id": str(new_id), "name": body.name, "code": body.code},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating customer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create customer")
@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    request: Request, customer_id: str, body: UpdateCustomerRequest
):
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
                "SELECT id, nama FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Customer not found")

            # Check for duplicate name if name is being changed
            if body.name and body.name != existing["nama"]:
                duplicate = await conn.fetchval(
                    "SELECT id FROM customers WHERE tenant_id = $1 AND nama = $2 AND id != $3",
                    ctx["tenant_id"],
                    body.name,
                    customer_id,
                )
                if duplicate:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Customer with name '{body.name}' already exists",
                    )

            # Build update query dynamically
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": True,
                    "message": "No changes provided",
                    "data": {"id": str(customer_id)},
                }

            # Map schema field names to database column names
            field_mapping = {
                "name": "nama",
                "code": "nomor_member",
                "phone": "telepon",
                "address": "alamat",
                # New fields use same name in both schema and DB
            }

            updates = []
            params = []
            param_idx = 1

            for field, value in update_data.items():
                # Handle special date field
                if field == "opening_balance_date" and value:
                    try:
                        from datetime import datetime
                        value = datetime.strptime(value, "%Y-%m-%d").date()
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid opening_balance_date format. Use YYYY-MM-DD"
                        )
                
                db_field = field_mapping.get(field, field)
                updates.append(f"{db_field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            updates.append("updated_at = NOW()")
            params.extend([customer_id, ctx["tenant_id"]])

            query = f"""
                UPDATE customers
                SET {", ".join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            logger.info(f"Customer updated: {customer_id}")

            return {
                "success": True,
                "message": "Customer updated successfully",
                "data": {"id": str(customer_id)},
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
async def delete_customer(request: Request, customer_id: str):
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
                "SELECT id, nama FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Customer not found")

            # Check for outstanding AR balance
            balance = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount - amount_paid), 0)
                FROM accounts_receivable
                WHERE tenant_id = $1
                  AND customer_id::text = $2
                  AND status IN ('OPEN', 'PARTIAL')
            """,
                ctx["tenant_id"],
                str(customer_id),
            )

            if balance and balance > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot delete customer with outstanding balance of Rp {balance:,}",
                )

            # Soft delete
            await conn.execute(
                """
                UPDATE customers
                SET is_active = false, updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                customer_id,
                ctx["tenant_id"],
            )

            logger.info(
                f"Customer soft deleted: {customer_id}, name={existing['nama']}"
            )

            return {
                "success": True,
                "message": "Customer deleted successfully",
                "data": {"id": str(customer_id), "name": existing["nama"]},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete customer")


# =============================================================================
# CUSTOMER OPEN INVOICES (for receive payments)
# =============================================================================


@router.get("/{customer_id}/open-invoices")
async def get_customer_open_invoices(
    request: Request,
    customer_id: str,  # customers.id is VARCHAR(255), not UUID
):
    """
    Get open (unpaid/partially paid) invoices for a customer.
    Used by receive payments to select invoices for allocation.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

            # Get invoices with remaining balance
            rows = await conn.fetch(
                """
                SELECT
                    id, invoice_number, invoice_date, due_date,
                    total_amount, amount_paid,
                    total_amount - COALESCE(amount_paid, 0) as remaining_amount,
                    CASE WHEN due_date < CURRENT_DATE THEN true ELSE false END as is_overdue,
                    GREATEST(0, CURRENT_DATE - due_date) as overdue_days
                FROM sales_invoices
                WHERE tenant_id = $1
                  AND customer_id = $2
                  AND status IN ('posted', 'partial', 'overdue')
                  AND total_amount > COALESCE(amount_paid, 0)
                ORDER BY due_date ASC, invoice_date ASC
            """,
                ctx["tenant_id"],
                customer_id,
            )

            invoices = [
                {
                    "id": str(row["id"]),
                    "invoice_number": row["invoice_number"],
                    "invoice_date": row["invoice_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "paid_amount": row["amount_paid"] or 0,
                    "remaining_amount": row["remaining_amount"],
                    "is_overdue": row["is_overdue"],
                    "overdue_days": row["overdue_days"],
                }
                for row in rows
            ]

            total_outstanding = sum(inv["remaining_amount"] for inv in invoices)
            total_overdue = sum(
                inv["remaining_amount"] for inv in invoices if inv["is_overdue"]
            )

            return {
                "invoices": invoices,
                "summary": {
                    "total_outstanding": total_outstanding,
                    "total_overdue": total_overdue,
                    "invoice_count": len(invoices),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting open invoices for customer {customer_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to get open invoices")


# =============================================================================
# CUSTOMER AVAILABLE DEPOSITS (for receive payments)
# =============================================================================


@router.get("/{customer_id}/available-deposits")
async def get_customer_available_deposits(
    request: Request,
    customer_id: str,  # customers.id is VARCHAR(255), not UUID
):
    """
    Get customer deposits with remaining balance.
    Used by receive payments when paying from deposit.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

            # Get deposits with remaining balance
            rows = await conn.fetch(
                """
                SELECT
                    id, deposit_number, deposit_date,
                    amount, amount_applied, amount_refunded,
                    amount - COALESCE(amount_applied, 0) - COALESCE(amount_refunded, 0) as remaining_amount
                FROM customer_deposits
                WHERE tenant_id = $1
                  AND customer_id::text = $2
                  AND status IN ('posted', 'partial')
                  AND amount > COALESCE(amount_applied, 0) + COALESCE(amount_refunded, 0)
                ORDER BY deposit_date ASC
            """,
                ctx["tenant_id"],
                str(customer_id),
            )

            deposits = [
                {
                    "id": str(row["id"]),
                    "deposit_number": row["deposit_number"],
                    "deposit_date": row["deposit_date"].isoformat(),
                    "amount": row["amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["remaining_amount"],
                }
                for row in rows
            ]

            total_available = sum(dep["remaining_amount"] for dep in deposits)

            return {
                "deposits": deposits,
                "total_available": total_available,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting available deposits for customer {customer_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to get available deposits")


# =============================================================================
# CUSTOMER NEXT CODE (for form auto-generation)
# =============================================================================


@router.get("/next-code")
async def get_next_customer_code(request: Request):
    """Get the next available customer code for auto-generation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT nomor_member FROM customers WHERE tenant_id = $1 AND nomor_member IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                ctx["tenant_id"],
            )
            import re

            if row and row["nomor_member"]:
                match = re.match(r"^([A-Z]*)([0-9]+)$", row["nomor_member"])
                if match:
                    prefix = match.group(1) or "C"
                    num = int(match.group(2)) + 1
                    next_code = f"{prefix}{num:04d}"
                else:
                    next_code = "C0001"
            else:
                next_code = "C0001"
            return {"success": True, "next_code": next_code}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting next customer code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get next code")


# =============================================================================
# CUSTOMER TRANSACTIONS HISTORY
# =============================================================================


@router.get("/{customer_id}/transactions")
async def get_customer_transactions(
    request: Request,
    customer_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get transaction history for a customer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit
        async with pool.acquire() as conn:
            customer = await conn.fetchrow(
                "SELECT id, nama FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id,
                ctx["tenant_id"],
            )
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT id::text, 'sales_invoice' as type, invoice_number as number,
                        invoice_date as date, total_amount as amount, 'Invoice' as description, status
                    FROM sales_invoices WHERE tenant_id = $1 AND customer_id = $2
                    UNION ALL
                    SELECT id::text, 'payment' as type, payment_number as number,
                        payment_date as date, total_amount as amount, 'Payment' as description, status
                    FROM receive_payments WHERE tenant_id = $1 AND customer_id = $2
                ) t ORDER BY date DESC LIMIT $3 OFFSET $4
            """,
                ctx["tenant_id"],
                customer_id,
                limit,
                offset,
            )
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT id FROM sales_invoices WHERE tenant_id = $1 AND customer_id = $2
                    UNION ALL SELECT id FROM receive_payments WHERE tenant_id = $1 AND customer_id::text = $2
                ) t
            """,
                ctx["tenant_id"],
                customer_id,
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
        logger.error(f"Error getting transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")


# =============================================================================
# CUSTOMER CREDIT INFO
# =============================================================================


@router.get("/{customer_id}/credit")
async def get_customer_credit(request: Request, customer_id: str):
    """Get customer's credit limit and usage information."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            customer = await conn.fetchrow(
                "SELECT id, nama, saldo_hutang, credit_limit FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id,
                ctx["tenant_id"],
            )
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")
            credit_limit = customer["credit_limit"] or 0
            used_credit = customer["saldo_hutang"] or 0
            return {
                "credit_limit": credit_limit,
                "used_credit": used_credit,
                "available_credit": max(0, credit_limit - used_credit),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get credit info")


# =============================================================================
# CUSTOMER OPENING BALANCE
# =============================================================================


@router.post("/{customer_id}/opening-balance")
async def set_customer_opening_balance(request: Request, customer_id: str):
    """Set or update customer opening AR balance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        amount = body.get("amount", 0)
        async with pool.acquire() as conn:
            customer = await conn.fetchrow(
                "SELECT id FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id,
                ctx["tenant_id"],
            )
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")
            await conn.execute(
                "UPDATE customers SET saldo_hutang = $1, updated_at = NOW() WHERE id = $2 AND tenant_id = $3",
                amount,
                customer_id,
                ctx["tenant_id"],
            )
            return {
                "success": True,
                "message": "Opening balance updated",
                "data": {"customer_id": customer_id, "amount": amount},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting opening balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set opening balance")


# =============================================================================
# CUSTOMER MERGE PREVIEW
# =============================================================================


@router.post("/merge/preview")
async def preview_customer_merge(request: Request):
    """Preview what will happen when merging customers."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        source_ids = body.get("source_ids", [])
        target_id = body.get("target_id")
        if not source_ids or not target_id:
            raise HTTPException(
                status_code=400, detail="source_ids and target_id required"
            )
        async with pool.acquire() as conn:
            sources = await conn.fetch(
                "SELECT id, nama, nomor_member, total_transaksi, saldo_hutang FROM customers WHERE id = ANY($1) AND tenant_id = $2",
                source_ids,
                ctx["tenant_id"],
            )
            target = await conn.fetchrow(
                "SELECT id, nama, nomor_member FROM customers WHERE id = $1 AND tenant_id = $2",
                target_id,
                ctx["tenant_id"],
            )
            if not target:
                raise HTTPException(status_code=404, detail="Target not found")
            invoice_count = await conn.fetchval(
                "SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND customer_id = ANY($2)",
                ctx["tenant_id"],
                source_ids,
            )
            return {
                "success": True,
                "preview": {
                    "source_customers": [
                        {"id": str(s["id"]), "name": s["nama"]} for s in sources
                    ],
                    "target_customer": {
                        "id": str(target["id"]),
                        "name": target["nama"],
                    },
                    "records_to_move": {"invoices": invoice_count},
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing merge: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to preview merge")


# =============================================================================
# CUSTOMER MERGE EXECUTE
# =============================================================================


@router.post("/merge")
async def merge_customers(request: Request):
    """Merge multiple customers into one target customer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        source_ids = body.get("source_ids", [])
        target_id = body.get("target_id")
        if not source_ids or not target_id:
            raise HTTPException(
                status_code=400, detail="source_ids and target_id required"
            )
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE sales_invoices SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = ANY($3)",
                    target_id,
                    ctx["tenant_id"],
                    source_ids,
                )
                await conn.execute(
                    "UPDATE customers SET is_active = false WHERE id = ANY($1) AND tenant_id = $2",
                    source_ids,
                    ctx["tenant_id"],
                )
            return {
                "success": True,
                "message": f"Merged {len(source_ids)} customers",
                "target_id": target_id,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error merging: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to merge")
