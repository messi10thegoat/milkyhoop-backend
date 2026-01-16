"""
Vendor Credits Router - Purchase Returns and AP Adjustments

Endpoints for managing vendor credits (kredit vendor).
Vendor credits reduce Accounts Payable and can be applied to bills or refunded by vendor.

Flow:
1. Create draft vendor credit
2. Post to accounting (creates AP reduction journal)
3. Apply to bill(s) OR receive refund from vendor
4. Void if needed (only if unapplied)

Endpoints:
- GET    /vendor-credits              - List vendor credits
- GET    /vendor-credits/summary      - Summary statistics
- GET    /vendor-credits/{id}         - Get vendor credit detail
- POST   /vendor-credits              - Create draft vendor credit
- PATCH  /vendor-credits/{id}         - Update draft vendor credit
- DELETE /vendor-credits/{id}         - Delete draft vendor credit
- POST   /vendor-credits/{id}/post    - Post to accounting
- POST   /vendor-credits/{id}/apply   - Apply to bill(s)
- POST   /vendor-credits/{id}/receive-refund - Record refund from vendor
- POST   /vendor-credits/{id}/void    - Void vendor credit
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..schemas.vendor_credits import (
    CreateVendorCreditRequest,
    UpdateVendorCreditRequest,
    ApplyVendorCreditRequest,
    ReceiveRefundRequest,
    VoidVendorCreditRequest,
    VendorCreditResponse,
    VendorCreditDetailResponse,
    VendorCreditListResponse,
    VendorCreditSummaryResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
AP_ACCOUNT = "2-10100"               # Hutang Usaha
PURCHASE_RETURN_ACCOUNT = "5-10300"  # Retur Pembelian
TAX_RECEIVABLE_ACCOUNT = "1-10700"   # PPN Masukan


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


def calculate_item_totals(item: dict) -> dict:
    """Calculate item totals with discount and tax."""
    quantity = Decimal(str(item.get('quantity', 0)))
    unit_price = Decimal(str(item.get('unit_price', 0)))
    discount_percent = Decimal(str(item.get('discount_percent', 0)))
    discount_amount = Decimal(str(item.get('discount_amount', 0)))
    tax_rate = Decimal(str(item.get('tax_rate', 0)))

    subtotal = quantity * unit_price

    # Apply discount (percent takes precedence)
    if discount_percent > 0:
        discount = subtotal * discount_percent / 100
    else:
        discount = discount_amount

    after_discount = subtotal - discount

    # Apply tax
    tax_amount = after_discount * tax_rate / 100

    total = after_discount + tax_amount

    return {
        **item,
        'subtotal': int(subtotal),
        'discount_amount': int(discount),
        'tax_amount': int(tax_amount),
        'total': int(total)
    }


# =============================================================================
# LIST VENDOR CREDITS
# =============================================================================

@router.get("", response_model=VendorCreditListResponse)
async def list_vendor_credits(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "partial", "applied", "void"]] = Query("all"),
    vendor_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by number or vendor name"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["credit_date", "credit_number", "total_amount", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List vendor credits with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(UUID(vendor_id))
                param_idx += 1

            if search:
                conditions.append(
                    f"(credit_number ILIKE ${param_idx} OR vendor_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"credit_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"credit_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "credit_date": "credit_date",
                "credit_number": "credit_number",
                "total_amount": "total_amount",
                "created_at": "created_at"
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM vendor_credits WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, credit_number, vendor_id, vendor_name,
                       credit_date, total_amount, amount_applied, amount_received,
                       status, reason, ref_no, created_at
                FROM vendor_credits
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "credit_number": row["credit_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "credit_date": row["credit_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_received": row["amount_received"] or 0,
                    "remaining_amount": row["total_amount"] - (row["amount_applied"] or 0) - (row["amount_received"] or 0),
                    "status": row["status"],
                    "reason": row["reason"],
                    "ref_no": row["ref_no"],
                    "created_at": row["created_at"].isoformat(),
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
        logger.error(f"Error listing vendor credits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list vendor credits")


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary", response_model=VendorCreditSummaryResponse)
async def get_vendor_credits_summary(request: Request):
    """Get summary statistics for vendor credits."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE status = 'applied') as applied_count,
                    COALESCE(SUM(total_amount), 0) as total_value,
                    COALESCE(SUM(amount_applied), 0) as total_applied,
                    COALESCE(SUM(amount_received), 0) as total_refunded,
                    COALESCE(SUM(total_amount - COALESCE(amount_applied, 0) - COALESCE(amount_received, 0))
                        FILTER (WHERE status IN ('posted', 'partial')), 0) as available_balance
                FROM vendor_credits
                WHERE tenant_id = $1 AND status != 'void'
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "partial_count": row["partial_count"] or 0,
                    "applied_count": row["applied_count"] or 0,
                    "total_value": int(row["total_value"] or 0),
                    "total_applied": int(row["total_applied"] or 0),
                    "total_refunded": int(row["total_refunded"] or 0),
                    "available_balance": int(row["available_balance"] or 0),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor credits summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET VENDOR CREDIT DETAIL
# =============================================================================

@router.get("/{vendor_credit_id}", response_model=VendorCreditDetailResponse)
async def get_vendor_credit(request: Request, vendor_credit_id: UUID):
    """Get detailed information for a vendor credit."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get vendor credit
            vc = await conn.fetchrow("""
                SELECT * FROM vendor_credits
                WHERE id = $1 AND tenant_id = $2
            """, vendor_credit_id, ctx["tenant_id"])

            if not vc:
                raise HTTPException(status_code=404, detail="Vendor credit not found")

            # Get items
            items = await conn.fetch("""
                SELECT * FROM vendor_credit_items
                WHERE vendor_credit_id = $1
                ORDER BY line_number
            """, vendor_credit_id)

            # Get applications with bill numbers
            applications = await conn.fetch("""
                SELECT a.*, b.invoice_number as bill_number
                FROM vendor_credit_applications a
                LEFT JOIN bills b ON a.bill_id = b.id
                WHERE a.vendor_credit_id = $1
                ORDER BY a.application_date
            """, vendor_credit_id)

            # Get refunds
            refunds = await conn.fetch("""
                SELECT * FROM vendor_credit_refunds
                WHERE vendor_credit_id = $1
                ORDER BY refund_date
            """, vendor_credit_id)

            # Build response
            remaining = vc["total_amount"] - (vc["amount_applied"] or 0) - (vc["amount_received"] or 0)

            return {
                "success": True,
                "data": {
                    "id": str(vc["id"]),
                    "credit_number": vc["credit_number"],
                    "vendor_id": str(vc["vendor_id"]) if vc["vendor_id"] else None,
                    "vendor_name": vc["vendor_name"],
                    "original_bill_id": str(vc["original_bill_id"]) if vc["original_bill_id"] else None,
                    "original_bill_number": vc["original_bill_number"],
                    "subtotal": vc["subtotal"],
                    "discount_percent": float(vc["discount_percent"] or 0),
                    "discount_amount": vc["discount_amount"] or 0,
                    "tax_rate": float(vc["tax_rate"] or 0),
                    "tax_amount": vc["tax_amount"] or 0,
                    "total_amount": vc["total_amount"],
                    "amount_applied": vc["amount_applied"] or 0,
                    "amount_received": vc["amount_received"] or 0,
                    "remaining_amount": remaining,
                    "status": vc["status"],
                    "credit_date": vc["credit_date"].isoformat(),
                    "reason": vc["reason"],
                    "reason_detail": vc["reason_detail"],
                    "ref_no": vc["ref_no"],
                    "notes": vc["notes"],
                    "ap_id": str(vc["ap_id"]) if vc["ap_id"] else None,
                    "journal_id": str(vc["journal_id"]) if vc["journal_id"] else None,
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"]) if item["item_id"] else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item["unit"],
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"] or 0,
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"] or 0,
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "batch_no": item["batch_no"],
                            "exp_date": item["exp_date"].isoformat() if item["exp_date"] else None,
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "applications": [
                        {
                            "id": str(app["id"]),
                            "bill_id": str(app["bill_id"]),
                            "bill_number": app["bill_number"],
                            "amount_applied": app["amount_applied"],
                            "application_date": app["application_date"].isoformat(),
                            "created_at": app["created_at"].isoformat(),
                        }
                        for app in applications
                    ],
                    "refunds": [
                        {
                            "id": str(ref["id"]),
                            "amount": ref["amount"],
                            "refund_date": ref["refund_date"].isoformat(),
                            "payment_method": ref["payment_method"],
                            "account_id": str(ref["account_id"]),
                            "reference": ref["reference"],
                            "created_at": ref["created_at"].isoformat(),
                        }
                        for ref in refunds
                    ],
                    "posted_at": vc["posted_at"].isoformat() if vc["posted_at"] else None,
                    "posted_by": str(vc["posted_by"]) if vc["posted_by"] else None,
                    "voided_at": vc["voided_at"].isoformat() if vc["voided_at"] else None,
                    "voided_reason": vc["voided_reason"],
                    "created_at": vc["created_at"].isoformat(),
                    "updated_at": vc["updated_at"].isoformat(),
                    "created_by": str(vc["created_by"]) if vc["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get vendor credit")


# =============================================================================
# CREATE VENDOR CREDIT (DRAFT)
# =============================================================================

@router.post("", response_model=VendorCreditResponse, status_code=201)
async def create_vendor_credit(request: Request, body: CreateVendorCreditRequest):
    """
    Create a new vendor credit in draft status.

    Draft vendor credits can be edited before posting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate vendor credit number
                vc_number = await conn.fetchval(
                    "SELECT generate_credit_number($1, 'VC')",
                    ctx["tenant_id"]
                )

                # Calculate items and totals
                calculated_items = [calculate_item_totals(item.model_dump()) for item in body.items]
                subtotal = sum(item['subtotal'] for item in calculated_items)
                total_tax = sum(item['tax_amount'] for item in calculated_items)

                # Apply overall discount
                if body.discount_percent > 0:
                    overall_discount = int(subtotal * Decimal(str(body.discount_percent)) / 100)
                else:
                    overall_discount = body.discount_amount

                # Apply overall tax if specified
                after_discount = subtotal - overall_discount
                if body.tax_rate > 0:
                    overall_tax = int(after_discount * Decimal(str(body.tax_rate)) / 100)
                else:
                    overall_tax = total_tax

                total_amount = after_discount + overall_tax

                # Get original bill number if provided
                original_bill_number = None
                if body.original_bill_id:
                    bill = await conn.fetchrow(
                        "SELECT invoice_number FROM bills WHERE id = $1",
                        UUID(body.original_bill_id)
                    )
                    if bill:
                        original_bill_number = bill["invoice_number"]

                # Insert vendor credit
                vc_id = await conn.fetchval("""
                    INSERT INTO vendor_credits (
                        tenant_id, credit_number, vendor_id, vendor_name,
                        original_bill_id, original_bill_number,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, credit_date, reason, reason_detail,
                        ref_no, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                              'draft', $13, $14, $15, $16, $17, $18)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    vc_number,
                    UUID(body.vendor_id) if body.vendor_id else None,
                    body.vendor_name,
                    UUID(body.original_bill_id) if body.original_bill_id else None,
                    original_bill_number,
                    subtotal,
                    body.discount_percent,
                    overall_discount,
                    body.tax_rate,
                    overall_tax,
                    total_amount,
                    body.credit_date,
                    body.reason,
                    body.reason_detail,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"]
                )

                # Insert items
                for idx, item in enumerate(calculated_items, 1):
                    await conn.execute("""
                        INSERT INTO vendor_credit_items (
                            vendor_credit_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, batch_no, exp_date, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                    """,
                        vc_id,
                        UUID(item['item_id']) if item.get('item_id') else None,
                        item.get('item_code'),
                        item['description'],
                        item['quantity'],
                        item.get('unit'),
                        item['unit_price'],
                        item.get('discount_percent', 0),
                        item.get('discount_amount', 0),
                        item.get('tax_code'),
                        item.get('tax_rate', 0),
                        item.get('tax_amount', 0),
                        item['subtotal'],
                        item['total'],
                        item.get('batch_no'),
                        item.get('exp_date'),
                        idx
                    )

                logger.info(f"Vendor credit created: {vc_id}, number={vc_number}")

                return {
                    "success": True,
                    "message": "Vendor credit created successfully",
                    "data": {
                        "id": str(vc_id),
                        "credit_number": vc_number,
                        "total_amount": total_amount,
                        "status": "draft"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating vendor credit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create vendor credit")


# =============================================================================
# UPDATE VENDOR CREDIT (DRAFT ONLY)
# =============================================================================

@router.patch("/{vendor_credit_id}", response_model=VendorCreditResponse)
async def update_vendor_credit(request: Request, vendor_credit_id: UUID, body: UpdateVendorCreditRequest):
    """
    Update a draft vendor credit.

    Only draft vendor credits can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check status
                vc = await conn.fetchrow("""
                    SELECT id, status FROM vendor_credits
                    WHERE id = $1 AND tenant_id = $2
                """, vendor_credit_id, ctx["tenant_id"])

                if not vc:
                    raise HTTPException(status_code=404, detail="Vendor credit not found")

                if vc["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft vendor credits can be updated"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(vendor_credit_id)}
                    }

                # Handle items if provided
                if "items" in update_data and update_data["items"]:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM vendor_credit_items WHERE vendor_credit_id = $1",
                        vendor_credit_id
                    )

                    # Calculate and insert new items
                    calculated_items = [
                        calculate_item_totals(item.model_dump())
                        for item in body.items
                    ]

                    subtotal = sum(item['subtotal'] for item in calculated_items)
                    total_tax = sum(item['tax_amount'] for item in calculated_items)

                    # Recalculate totals
                    discount_percent = update_data.get('discount_percent', 0)
                    discount_amount = update_data.get('discount_amount', 0)
                    tax_rate = update_data.get('tax_rate', 0)

                    if discount_percent > 0:
                        overall_discount = int(subtotal * Decimal(str(discount_percent)) / 100)
                    else:
                        overall_discount = discount_amount

                    after_discount = subtotal - overall_discount

                    if tax_rate > 0:
                        overall_tax = int(after_discount * Decimal(str(tax_rate)) / 100)
                    else:
                        overall_tax = total_tax

                    total_amount = after_discount + overall_tax

                    # Update totals
                    await conn.execute("""
                        UPDATE vendor_credits
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """, vendor_credit_id, subtotal, overall_discount, overall_tax, total_amount)

                    # Insert new items
                    for idx, item in enumerate(calculated_items, 1):
                        await conn.execute("""
                            INSERT INTO vendor_credit_items (
                                vendor_credit_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, batch_no, exp_date, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                        """,
                            vendor_credit_id,
                            UUID(item['item_id']) if item.get('item_id') else None,
                            item.get('item_code'),
                            item['description'],
                            item['quantity'],
                            item.get('unit'),
                            item['unit_price'],
                            item.get('discount_percent', 0),
                            item.get('discount_amount', 0),
                            item.get('tax_code'),
                            item.get('tax_rate', 0),
                            item.get('tax_amount', 0),
                            item['subtotal'],
                            item['total'],
                            item.get('batch_no'),
                            item.get('exp_date'),
                            idx
                        )

                    del update_data["items"]

                # Update other fields
                if update_data:
                    excluded = {"items"}
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field in excluded:
                            continue
                        updates.append(f"{field} = ${param_idx}")
                        if field in ("vendor_id", "original_bill_id") and value:
                            params.append(UUID(value))
                        else:
                            params.append(value)
                        param_idx += 1

                    if updates:
                        params.extend([vendor_credit_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE vendor_credits
                            SET {', '.join(updates)}, updated_at = NOW()
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Vendor credit updated: {vendor_credit_id}")

                return {
                    "success": True,
                    "message": "Vendor credit updated successfully",
                    "data": {"id": str(vendor_credit_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update vendor credit")


# =============================================================================
# DELETE VENDOR CREDIT (DRAFT ONLY)
# =============================================================================

@router.delete("/{vendor_credit_id}", response_model=VendorCreditResponse)
async def delete_vendor_credit(request: Request, vendor_credit_id: UUID):
    """
    Delete a draft vendor credit.

    Only draft vendor credits can be deleted. Use void for posted vendor credits.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check status
            vc = await conn.fetchrow("""
                SELECT id, status, credit_number FROM vendor_credits
                WHERE id = $1 AND tenant_id = $2
            """, vendor_credit_id, ctx["tenant_id"])

            if not vc:
                raise HTTPException(status_code=404, detail="Vendor credit not found")

            if vc["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft vendor credits can be deleted. Use void for posted."
                )

            # Delete (cascade will delete items)
            await conn.execute(
                "DELETE FROM vendor_credits WHERE id = $1",
                vendor_credit_id
            )

            logger.info(f"Vendor credit deleted: {vendor_credit_id}")

            return {
                "success": True,
                "message": "Vendor credit deleted successfully",
                "data": {
                    "id": str(vendor_credit_id),
                    "credit_number": vc["credit_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete vendor credit")


# =============================================================================
# POST VENDOR CREDIT TO ACCOUNTING
# =============================================================================

@router.post("/{vendor_credit_id}/post", response_model=VendorCreditResponse)
async def post_vendor_credit(request: Request, vendor_credit_id: UUID):
    """
    Post vendor credit to accounting.

    Creates journal entry:
    - Dr. Accounts Payable
    - Cr. Purchase Returns (Retur Pembelian)
    - Cr. VAT Receivable (if tax)

    Changes status from 'draft' to 'posted'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get vendor credit
                vc = await conn.fetchrow("""
                    SELECT * FROM vendor_credits
                    WHERE id = $1 AND tenant_id = $2
                """, vendor_credit_id, ctx["tenant_id"])

                if not vc:
                    raise HTTPException(status_code=404, detail="Vendor credit not found")

                if vc["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post vendor credit with status '{vc['status']}'"
                    )

                # Get account IDs
                ap_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], AP_ACCOUNT)

                purchase_return_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], PURCHASE_RETURN_ACCOUNT)

                if not ap_account_id or not purchase_return_account_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Required accounts not found in CoA"
                    )

                # Generate journal number
                import uuid as uuid_module
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                journal_number = await conn.fetchval("""
                    SELECT get_next_journal_number($1, 'VC')
                """, ctx["tenant_id"])

                if not journal_number:
                    journal_number = f"VC-{vc['credit_number']}"

                # Calculate amounts
                total_amount = vc["total_amount"]
                tax_amount = vc["tax_amount"] or 0
                subtotal = total_amount - tax_amount

                # Create journal entry
                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'VENDOR_CREDIT', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    vc["credit_date"],
                    f"Vendor Credit {vc['credit_number']} - {vc['vendor_name']}",
                    vendor_credit_id,
                    str(trace_id),
                    float(total_amount),
                    ctx["user_id"]
                )

                # Journal lines
                line_number = 1

                # Dr. Accounts Payable
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    ap_account_id,
                    float(total_amount),
                    f"Pengurangan Hutang - {vc['credit_number']}"
                )
                line_number += 1

                # Cr. Purchase Returns (subtotal)
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    purchase_return_account_id,
                    float(subtotal),
                    f"Retur Pembelian - {vc['credit_number']}"
                )
                line_number += 1

                # Cr. VAT Receivable (if tax)
                if tax_amount > 0:
                    tax_account_id = await conn.fetchval("""
                        SELECT id FROM chart_of_accounts
                        WHERE tenant_id = $1 AND account_code = $2
                    """, ctx["tenant_id"], TAX_RECEIVABLE_ACCOUNT)

                    if tax_account_id:
                        await conn.execute("""
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                            uuid_module.uuid4(),
                            journal_id,
                            line_number,
                            tax_account_id,
                            float(tax_amount),
                            f"PPN Retur - {vc['credit_number']}"
                        )

                # Update vendor credit status
                await conn.execute("""
                    UPDATE vendor_credits
                    SET status = 'posted', journal_id = $2,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1
                """, vendor_credit_id, journal_id, ctx["user_id"])

                logger.info(f"Vendor credit posted: {vendor_credit_id}, journal={journal_id}")

                return {
                    "success": True,
                    "message": "Vendor credit posted to accounting",
                    "data": {
                        "id": str(vendor_credit_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post vendor credit")


# =============================================================================
# APPLY VENDOR CREDIT TO BILL(S)
# =============================================================================

@router.post("/{vendor_credit_id}/apply", response_model=VendorCreditResponse)
async def apply_vendor_credit(request: Request, vendor_credit_id: UUID, body: ApplyVendorCreditRequest):
    """
    Apply vendor credit to one or more bills.

    Reduces the bill's outstanding balance.
    Vendor credit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get vendor credit
                vc = await conn.fetchrow("""
                    SELECT * FROM vendor_credits
                    WHERE id = $1 AND tenant_id = $2
                """, vendor_credit_id, ctx["tenant_id"])

                if not vc:
                    raise HTTPException(status_code=404, detail="Vendor credit not found")

                if vc["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot apply vendor credit with status '{vc['status']}'"
                    )

                # Calculate remaining
                remaining = vc["total_amount"] - (vc["amount_applied"] or 0) - (vc["amount_received"] or 0)
                total_to_apply = sum(app.amount for app in body.applications)

                if total_to_apply > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Application amount ({total_to_apply}) exceeds remaining balance ({remaining})"
                    )

                application_date = body.application_date or date.today()
                applications_created = []

                for app in body.applications:
                    # Validate bill
                    bill = await conn.fetchrow("""
                        SELECT id, vendor_id, amount, grand_total, amount_paid, status_v2 as status
                        FROM bills
                        WHERE id = $1 AND tenant_id = $2
                    """, UUID(app.bill_id), ctx["tenant_id"])

                    if not bill:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Bill {app.bill_id} not found"
                        )

                    # Verify same vendor
                    if vc["vendor_id"] and bill["vendor_id"] != vc["vendor_id"]:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Bill {app.bill_id} belongs to different vendor"
                        )

                    # Check bill has balance
                    bill_total = bill["grand_total"] or bill["amount"]
                    bill_remaining = bill_total - (bill["amount_paid"] or 0)
                    if app.amount > bill_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Application amount exceeds bill remaining balance"
                        )

                    # Check for existing application
                    existing = await conn.fetchval("""
                        SELECT id FROM vendor_credit_applications
                        WHERE vendor_credit_id = $1 AND bill_id = $2
                    """, vendor_credit_id, UUID(app.bill_id))

                    if existing:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Vendor credit already applied to bill {app.bill_id}"
                        )

                    # Create application
                    import uuid as uuid_module
                    app_id = uuid_module.uuid4()

                    await conn.execute("""
                        INSERT INTO vendor_credit_applications (
                            id, tenant_id, vendor_credit_id, bill_id,
                            amount_applied, application_date, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        app_id,
                        ctx["tenant_id"],
                        vendor_credit_id,
                        UUID(app.bill_id),
                        app.amount,
                        application_date,
                        ctx["user_id"]
                    )

                    # Update bill
                    new_amount_paid = (bill["amount_paid"] or 0) + app.amount
                    new_status = "paid" if new_amount_paid >= bill_total else "posted"

                    await conn.execute("""
                        UPDATE bills
                        SET amount_paid = $2, status_v2 = $3, updated_at = NOW()
                        WHERE id = $1
                    """, UUID(app.bill_id), new_amount_paid, new_status)

                    # Update AP if exists
                    await conn.execute("""
                        UPDATE accounts_payable
                        SET amount_paid = amount_paid + $2,
                            status = CASE
                                WHEN amount_paid + $2 >= amount THEN 'PAID'
                                ELSE 'PARTIAL'
                            END,
                            updated_at = NOW()
                        WHERE source_id = $1 AND source_type = 'BILL'
                    """, UUID(app.bill_id), app.amount)

                    applications_created.append({
                        "application_id": str(app_id),
                        "bill_id": app.bill_id,
                        "amount": app.amount
                    })

                # Vendor credit status will be updated by trigger
                logger.info(f"Vendor credit applied: {vendor_credit_id}, applications={len(applications_created)}")

                return {
                    "success": True,
                    "message": f"Vendor credit applied to {len(applications_created)} bill(s)",
                    "data": {
                        "id": str(vendor_credit_id),
                        "applications": applications_created
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply vendor credit")


# =============================================================================
# RECEIVE REFUND FROM VENDOR
# =============================================================================

@router.post("/{vendor_credit_id}/receive-refund", response_model=VendorCreditResponse)
async def receive_refund(request: Request, vendor_credit_id: UUID, body: ReceiveRefundRequest):
    """
    Record cash refund received from vendor.

    Creates journal entry:
    - Dr. Cash/Bank
    - Cr. Accounts Payable

    Vendor credit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get vendor credit
                vc = await conn.fetchrow("""
                    SELECT * FROM vendor_credits
                    WHERE id = $1 AND tenant_id = $2
                """, vendor_credit_id, ctx["tenant_id"])

                if not vc:
                    raise HTTPException(status_code=404, detail="Vendor credit not found")

                if vc["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot receive refund for vendor credit with status '{vc['status']}'"
                    )

                # Check remaining
                remaining = vc["total_amount"] - (vc["amount_applied"] or 0) - (vc["amount_received"] or 0)

                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount ({body.amount}) exceeds remaining balance ({remaining})"
                    )

                # Validate account
                account = await conn.fetchrow("""
                    SELECT id, account_code as code, name FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.account_id), ctx["tenant_id"])

                if not account:
                    raise HTTPException(status_code=400, detail="Payment account not found")

                import uuid as uuid_module
                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                # Create refund journal
                journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, 'RR')",
                    ctx["tenant_id"]
                ) or f"RR-{vc['credit_number']}"

                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'VENDOR_CREDIT_REFUND', $6, $7, 'POSTED', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund Received {vc['credit_number']} - {vc['vendor_name']}",
                    vendor_credit_id,
                    str(trace_id),
                    float(body.amount),
                    ctx["user_id"]
                )

                # Get AP account
                ap_account_id = await conn.fetchval("""
                    SELECT id FROM chart_of_accounts
                    WHERE tenant_id = $1 AND account_code = $2
                """, ctx["tenant_id"], AP_ACCOUNT)

                # Dr. Cash/Bank
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    UUID(body.account_id),
                    float(body.amount),
                    f"Terima Refund - {vc['credit_number']}"
                )

                # Cr. AP
                await conn.execute("""
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    ap_account_id,
                    float(body.amount),
                    f"Hutang Refund - {vc['credit_number']}"
                )

                # Create refund record
                await conn.execute("""
                    INSERT INTO vendor_credit_refunds (
                        id, tenant_id, vendor_credit_id, amount, refund_date,
                        payment_method, account_id, bank_account_id,
                        reference, notes, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    refund_id,
                    ctx["tenant_id"],
                    vendor_credit_id,
                    body.amount,
                    body.refund_date,
                    body.payment_method,
                    UUID(body.account_id),
                    UUID(body.bank_account_id) if body.bank_account_id else None,
                    body.reference,
                    body.notes,
                    journal_id,
                    ctx["user_id"]
                )

                # Status will be updated by trigger
                logger.info(f"Vendor credit refund received: {vendor_credit_id}, amount={body.amount}")

                return {
                    "success": True,
                    "message": "Refund received successfully",
                    "data": {
                        "id": str(vendor_credit_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error receiving refund for vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to receive refund")


# =============================================================================
# VOID VENDOR CREDIT
# =============================================================================

@router.post("/{vendor_credit_id}/void", response_model=VendorCreditResponse)
async def void_vendor_credit(request: Request, vendor_credit_id: UUID, body: VoidVendorCreditRequest):
    """
    Void a vendor credit.

    Creates reversal journal entry.
    Vendor credit must have no applications or refunds.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get vendor credit
                vc = await conn.fetchrow("""
                    SELECT * FROM vendor_credits
                    WHERE id = $1 AND tenant_id = $2
                """, vendor_credit_id, ctx["tenant_id"])

                if not vc:
                    raise HTTPException(status_code=404, detail="Vendor credit not found")

                if vc["status"] == "void":
                    raise HTTPException(status_code=400, detail="Vendor credit already voided")

                if vc["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM vendor_credits WHERE id = $1",
                        vendor_credit_id
                    )
                    return {
                        "success": True,
                        "message": "Draft vendor credit deleted",
                        "data": {"id": str(vendor_credit_id)}
                    }

                # Check for applications or refunds
                if (vc["amount_applied"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void vendor credit with applications. Reverse applications first."
                    )

                if (vc["amount_received"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void vendor credit with refunds. Reverse refunds first."
                    )

                # Create reversal journal if original was posted
                if vc["journal_id"]:
                    import uuid as uuid_module
                    reversal_journal_id = uuid_module.uuid4()

                    # Get original journal lines
                    original_lines = await conn.fetch("""
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """, vc["journal_id"])

                    journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RV')",
                        ctx["tenant_id"]
                    ) or f"RV-{vc['credit_number']}"

                    # Create reversal header
                    await conn.execute("""
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'VENDOR_CREDIT', $5, $6, 'POSTED', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {vc['credit_number']} - {vc['vendor_name']}",
                        vendor_credit_id,
                        vc["journal_id"],
                        float(vc["total_amount"]),
                        ctx["user_id"]
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute("""
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap
                            line["debit"],   # Swap
                            f"Reversal - {line['memo'] or ''}"
                        )

                    # Mark original journal as reversed
                    await conn.execute("""
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                    """, vc["journal_id"], reversal_journal_id)

                # Update vendor credit status
                await conn.execute("""
                    UPDATE vendor_credits
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """, vendor_credit_id, ctx["user_id"], body.reason)

                logger.info(f"Vendor credit voided: {vendor_credit_id}")

                return {
                    "success": True,
                    "message": "Vendor credit voided successfully",
                    "data": {
                        "id": str(vendor_credit_id),
                        "status": "void"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void vendor credit")
