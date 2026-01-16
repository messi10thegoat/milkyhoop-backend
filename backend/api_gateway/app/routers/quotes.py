"""
Quotes (Penawaran) Router
Pre-sale quotes before conversion to Invoice or Sales Order.
NO journal entries - accounting impact happens on conversion.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from datetime import date, datetime
from decimal import Decimal
import asyncpg
import logging
import uuid as uuid_module

from ..config import settings
from ..schemas.quotes import (
    CreateQuoteRequest, UpdateQuoteRequest,
    SendQuoteRequest, DeclineQuoteRequest,
    ConvertToInvoiceRequest, ConvertToOrderRequest, DuplicateQuoteRequest,
    QuoteListResponse, QuoteDetailResponse, QuoteResponse,
    QuoteSummaryResponse, ExpiringQuotesResponse,
    QuoteListItem, QuoteDetail, QuoteItemResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
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
    """Extract user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": uuid_module.UUID(user_id) if user_id else None
    }


def calculate_item_totals(item: dict) -> dict:
    """Calculate line item totals."""
    quantity = Decimal(str(item.get('quantity', 1)))
    unit_price = Decimal(str(item.get('unit_price', 0)))
    discount_percent = Decimal(str(item.get('discount_percent', 0)))
    tax_rate = Decimal(str(item.get('tax_rate', 0)))

    subtotal = quantity * unit_price
    discount = subtotal * discount_percent / 100
    after_discount = subtotal - discount
    tax_amount = after_discount * tax_rate / 100
    line_total = after_discount + tax_amount

    return {
        **item,
        'tax_amount': int(tax_amount),
        'line_total': int(line_total)
    }


def calculate_quote_totals(items: list, discount_type: str, discount_value: float) -> dict:
    """Calculate quote totals from items."""
    subtotal = sum(item.get('line_total', 0) - item.get('tax_amount', 0) for item in items)
    total_tax = sum(item.get('tax_amount', 0) for item in items)

    if discount_type == 'percentage':
        discount_amount = int(Decimal(str(subtotal)) * Decimal(str(discount_value)) / 100)
    else:
        discount_amount = int(discount_value)

    total_amount = subtotal - discount_amount + total_tax

    return {
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'tax_amount': total_tax,
        'total_amount': total_amount
    }


# ============================================================================
# LIST & DETAIL ENDPOINTS
# ============================================================================

@router.get("", response_model=QuoteListResponse)
async def list_quotes(
    request: Request,
    status: Optional[Literal['all', 'draft', 'sent', 'viewed', 'accepted', 'declined', 'expired', 'converted']] = Query('all'),
    customer_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100)
):
    """List quotes with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query
            conditions = ["tenant_id = $1"]
            params = [ctx['tenant_id']]
            param_idx = 2

            if status != 'all':
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(uuid_module.UUID(customer_id))
                param_idx += 1

            if start_date:
                conditions.append(f"quote_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"quote_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if search:
                conditions.append(f"(quote_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR subject ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count
            count_query = f"SELECT COUNT(*) FROM quotes WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # List
            list_query = f"""
                SELECT id, quote_number, quote_date, expiry_date, customer_id, customer_name,
                       subject, subtotal, discount_amount, tax_amount, total_amount, status,
                       converted_to_type, converted_to_id, created_at
                FROM quotes
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(list_query, *params)

            items = []
            for row in rows:
                is_expired = (
                    row['expiry_date'] is not None
                    and row['expiry_date'] < date.today()
                    and row['status'] == 'sent'
                )
                items.append(QuoteListItem(
                    id=str(row['id']),
                    quote_number=row['quote_number'],
                    quote_date=row['quote_date'].isoformat(),
                    expiry_date=row['expiry_date'].isoformat() if row['expiry_date'] else None,
                    customer_id=str(row['customer_id']),
                    customer_name=row['customer_name'],
                    subject=row['subject'],
                    subtotal=row['subtotal'],
                    discount_amount=row['discount_amount'],
                    tax_amount=row['tax_amount'],
                    total_amount=row['total_amount'],
                    status=row['status'],
                    converted_to_type=row['converted_to_type'],
                    converted_to_id=str(row['converted_to_id']) if row['converted_to_id'] else None,
                    created_at=row['created_at'].isoformat(),
                    is_expired=is_expired
                ))

            return QuoteListResponse(
                items=items,
                total=total,
                has_more=(skip + limit) < total
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing quotes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list quotes")


@router.get("/expiring", response_model=ExpiringQuotesResponse)
async def get_expiring_quotes(
    request: Request,
    days: int = Query(7, ge=1, le=30, description="Days until expiry")
):
    """Get quotes expiring within specified days."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT id, quote_number, customer_name, expiry_date, total_amount
                FROM quotes
                WHERE tenant_id = $1
                AND status = 'sent'
                AND expiry_date IS NOT NULL
                AND expiry_date <= CURRENT_DATE + $2
                AND expiry_date >= CURRENT_DATE
                ORDER BY expiry_date ASC
            """
            rows = await conn.fetch(query, ctx['tenant_id'], days)

            items = []
            for row in rows:
                days_until = (row['expiry_date'] - date.today()).days
                items.append({
                    "id": str(row['id']),
                    "quote_number": row['quote_number'],
                    "customer_name": row['customer_name'],
                    "expiry_date": row['expiry_date'].isoformat(),
                    "total_amount": row['total_amount'],
                    "days_until_expiry": days_until
                })

            return ExpiringQuotesResponse(
                success=True,
                data=items,
                total=len(items)
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting expiring quotes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get expiring quotes")


@router.get("/summary", response_model=QuoteSummaryResponse)
async def get_quote_summary(request: Request):
    """Get quote statistics summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total_quotes,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'sent') as sent_count,
                    COUNT(*) FILTER (WHERE status = 'accepted') as accepted_count,
                    COUNT(*) FILTER (WHERE status = 'declined') as declined_count,
                    COUNT(*) FILTER (WHERE status = 'expired') as expired_count,
                    COUNT(*) FILTER (WHERE status = 'converted') as converted_count,
                    COALESCE(SUM(total_amount), 0) as total_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'accepted'), 0) as accepted_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'sent'), 0) as pending_value
                FROM quotes
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx['tenant_id'])

            return QuoteSummaryResponse(
                success=True,
                data={
                    "total_quotes": row['total_quotes'],
                    "draft_count": row['draft_count'],
                    "sent_count": row['sent_count'],
                    "accepted_count": row['accepted_count'],
                    "declined_count": row['declined_count'],
                    "expired_count": row['expired_count'],
                    "converted_count": row['converted_count'],
                    "total_value": row['total_value'],
                    "accepted_value": row['accepted_value'],
                    "pending_value": row['pending_value']
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quote summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get quote summary")


@router.get("/{quote_id}", response_model=QuoteDetailResponse)
async def get_quote_detail(request: Request, quote_id: str):
    """Get quote detail with items."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get quote header
            quote_query = """
                SELECT * FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """
            quote = await conn.fetchrow(quote_query, uuid_module.UUID(quote_id), ctx['tenant_id'])

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            # Get items
            items_query = """
                SELECT * FROM quote_items
                WHERE quote_id = $1
                ORDER BY sort_order, id
            """
            items = await conn.fetch(items_query, uuid_module.UUID(quote_id))

            is_expired = (
                quote['expiry_date'] is not None
                and quote['expiry_date'] < date.today()
                and quote['status'] == 'sent'
            )

            return QuoteDetailResponse(
                success=True,
                data=QuoteDetail(
                    id=str(quote['id']),
                    quote_number=quote['quote_number'],
                    quote_date=quote['quote_date'].isoformat(),
                    expiry_date=quote['expiry_date'].isoformat() if quote['expiry_date'] else None,
                    customer_id=str(quote['customer_id']),
                    customer_name=quote['customer_name'],
                    customer_email=quote['customer_email'],
                    reference=quote['reference'],
                    subject=quote['subject'],
                    subtotal=quote['subtotal'],
                    discount_type=quote['discount_type'],
                    discount_value=float(quote['discount_value']),
                    discount_amount=quote['discount_amount'],
                    tax_amount=quote['tax_amount'],
                    total_amount=quote['total_amount'],
                    status=quote['status'],
                    converted_to_type=quote['converted_to_type'],
                    converted_to_id=str(quote['converted_to_id']) if quote['converted_to_id'] else None,
                    converted_at=quote['converted_at'].isoformat() if quote['converted_at'] else None,
                    notes=quote['notes'],
                    terms=quote['terms'],
                    footer=quote['footer'],
                    items=[QuoteItemResponse(
                        id=str(item['id']),
                        item_id=str(item['item_id']) if item['item_id'] else None,
                        description=item['description'],
                        quantity=float(item['quantity']),
                        unit=item['unit'],
                        unit_price=item['unit_price'],
                        discount_percent=float(item['discount_percent']),
                        tax_id=str(item['tax_id']) if item['tax_id'] else None,
                        tax_rate=float(item['tax_rate']),
                        tax_amount=item['tax_amount'],
                        line_total=item['line_total'],
                        group_name=item['group_name'],
                        sort_order=item['sort_order']
                    ) for item in items],
                    created_at=quote['created_at'].isoformat(),
                    updated_at=quote['updated_at'].isoformat(),
                    created_by=str(quote['created_by']) if quote['created_by'] else None,
                    sent_at=quote['sent_at'].isoformat() if quote['sent_at'] else None,
                    viewed_at=quote['viewed_at'].isoformat() if quote['viewed_at'] else None,
                    accepted_at=quote['accepted_at'].isoformat() if quote['accepted_at'] else None,
                    declined_at=quote['declined_at'].isoformat() if quote['declined_at'] else None,
                    declined_reason=quote['declined_reason'],
                    is_expired=is_expired
                )
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quote detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get quote detail")


# ============================================================================
# CREATE, UPDATE, DELETE ENDPOINTS
# ============================================================================

@router.post("", response_model=QuoteResponse)
async def create_quote(request: Request, body: CreateQuoteRequest):
    """Create a new quote (draft status)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate quote number
                quote_number = await conn.fetchval(
                    "SELECT generate_quote_number($1, 'QUO')",
                    ctx['tenant_id']
                )

                # Calculate item totals
                calculated_items = [calculate_item_totals(item.model_dump()) for item in body.items]

                # Calculate quote totals
                totals = calculate_quote_totals(calculated_items, body.discount_type, body.discount_value)

                # Create quote
                quote_id = uuid_module.uuid4()
                await conn.execute("""
                    INSERT INTO quotes (
                        id, tenant_id, quote_number, quote_date, expiry_date,
                        customer_id, customer_name, customer_email,
                        reference, subject,
                        subtotal, discount_type, discount_value, discount_amount,
                        tax_amount, total_amount, status,
                        notes, terms, footer, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, 'draft',
                        $17, $18, $19, $20
                    )
                """,
                    quote_id, ctx['tenant_id'], quote_number, body.quote_date, body.expiry_date,
                    uuid_module.UUID(body.customer_id), body.customer_name, body.customer_email,
                    body.reference, body.subject,
                    totals['subtotal'], body.discount_type, body.discount_value, totals['discount_amount'],
                    totals['tax_amount'], totals['total_amount'],
                    body.notes, body.terms, body.footer, ctx['user_id']
                )

                # Create items
                for idx, item in enumerate(calculated_items):
                    await conn.execute("""
                        INSERT INTO quote_items (
                            id, quote_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total,
                            group_name, sort_order
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                        )
                    """,
                        uuid_module.uuid4(), quote_id,
                        uuid_module.UUID(item['item_id']) if item.get('item_id') else None,
                        item['description'], item['quantity'], item.get('unit'),
                        item['unit_price'], item.get('discount_percent', 0),
                        uuid_module.UUID(item['tax_id']) if item.get('tax_id') else None,
                        item.get('tax_rate', 0), item['tax_amount'], item['line_total'],
                        item.get('group_name'), item.get('sort_order', idx)
                    )

                return QuoteResponse(
                    success=True,
                    message="Quote created successfully",
                    data={
                        "id": str(quote_id),
                        "quote_number": quote_number,
                        "total_amount": totals['total_amount']
                    }
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create quote")


@router.patch("/{quote_id}", response_model=QuoteResponse)
async def update_quote(request: Request, quote_id: str, body: UpdateQuoteRequest):
    """Update an existing quote (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check quote exists and is draft
                quote = await conn.fetchrow("""
                    SELECT id, status FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """, uuid_module.UUID(quote_id), ctx['tenant_id'])

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                if quote['status'] != 'draft':
                    raise HTTPException(status_code=400, detail="Only draft quotes can be updated")

                # Build update query
                updates = []
                params = []
                param_idx = 1

                update_fields = {
                    'quote_date': body.quote_date,
                    'expiry_date': body.expiry_date,
                    'customer_id': uuid_module.UUID(body.customer_id) if body.customer_id else None,
                    'customer_name': body.customer_name,
                    'customer_email': body.customer_email,
                    'reference': body.reference,
                    'subject': body.subject,
                    'discount_type': body.discount_type,
                    'discount_value': body.discount_value,
                    'notes': body.notes,
                    'terms': body.terms,
                    'footer': body.footer
                }

                for field, value in update_fields.items():
                    if value is not None:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                # Update items if provided
                if body.items is not None:
                    # Delete existing items
                    await conn.execute("DELETE FROM quote_items WHERE quote_id = $1", uuid_module.UUID(quote_id))

                    # Calculate and insert new items
                    calculated_items = [calculate_item_totals(item.model_dump()) for item in body.items]
                    discount_type = body.discount_type or 'fixed'
                    discount_value = body.discount_value or 0

                    # Get current discount info if not provided
                    if body.discount_type is None or body.discount_value is None:
                        current = await conn.fetchrow(
                            "SELECT discount_type, discount_value FROM quotes WHERE id = $1",
                            uuid_module.UUID(quote_id)
                        )
                        discount_type = body.discount_type or current['discount_type']
                        discount_value = body.discount_value if body.discount_value is not None else float(current['discount_value'])

                    totals = calculate_quote_totals(calculated_items, discount_type, discount_value)

                    # Add totals to update
                    updates.append(f"subtotal = ${param_idx}")
                    params.append(totals['subtotal'])
                    param_idx += 1

                    updates.append(f"discount_amount = ${param_idx}")
                    params.append(totals['discount_amount'])
                    param_idx += 1

                    updates.append(f"tax_amount = ${param_idx}")
                    params.append(totals['tax_amount'])
                    param_idx += 1

                    updates.append(f"total_amount = ${param_idx}")
                    params.append(totals['total_amount'])
                    param_idx += 1

                    # Insert new items
                    for idx, item in enumerate(calculated_items):
                        await conn.execute("""
                            INSERT INTO quote_items (
                                id, quote_id, item_id, description,
                                quantity, unit, unit_price, discount_percent,
                                tax_id, tax_rate, tax_amount, line_total,
                                group_name, sort_order
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                            )
                        """,
                            uuid_module.uuid4(), uuid_module.UUID(quote_id),
                            uuid_module.UUID(item['item_id']) if item.get('item_id') else None,
                            item['description'], item['quantity'], item.get('unit'),
                            item['unit_price'], item.get('discount_percent', 0),
                            uuid_module.UUID(item['tax_id']) if item.get('tax_id') else None,
                            item.get('tax_rate', 0), item['tax_amount'], item['line_total'],
                            item.get('group_name'), item.get('sort_order', idx)
                        )

                if updates:
                    params.append(uuid_module.UUID(quote_id))
                    params.append(ctx['tenant_id'])
                    update_query = f"""
                        UPDATE quotes SET {', '.join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(update_query, *params)

                return QuoteResponse(
                    success=True,
                    message="Quote updated successfully",
                    data={"id": quote_id}
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update quote")


@router.delete("/{quote_id}", response_model=QuoteResponse)
async def delete_quote(request: Request, quote_id: str):
    """Delete a quote (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check quote exists and is draft
            quote = await conn.fetchrow("""
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote['status'] != 'draft':
                raise HTTPException(status_code=400, detail="Only draft quotes can be deleted")

            # Delete (cascade deletes items)
            await conn.execute("""
                DELETE FROM quotes WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            return QuoteResponse(
                success=True,
                message="Quote deleted successfully",
                data={"quote_number": quote['quote_number']}
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete quote")


# ============================================================================
# WORKFLOW ENDPOINTS
# ============================================================================

@router.post("/{quote_id}/send", response_model=QuoteResponse)
async def send_quote(request: Request, quote_id: str, body: SendQuoteRequest = None):
    """Mark quote as sent."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check quote
            quote = await conn.fetchrow("""
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote['status'] not in ('draft', 'sent'):
                raise HTTPException(status_code=400, detail=f"Cannot send quote with status '{quote['status']}'")

            # Update status
            await conn.execute("""
                UPDATE quotes SET status = 'sent', sent_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            # TODO: Send email if body.send_email is True

            return QuoteResponse(
                success=True,
                message="Quote sent successfully",
                data={"quote_number": quote['quote_number'], "status": "sent"}
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send quote")


@router.post("/{quote_id}/accept", response_model=QuoteResponse)
async def accept_quote(request: Request, quote_id: str):
    """Mark quote as accepted."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            quote = await conn.fetchrow("""
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote['status'] not in ('sent', 'viewed'):
                raise HTTPException(status_code=400, detail=f"Cannot accept quote with status '{quote['status']}'")

            await conn.execute("""
                UPDATE quotes SET status = 'accepted', accepted_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            return QuoteResponse(
                success=True,
                message="Quote accepted",
                data={"quote_number": quote['quote_number'], "status": "accepted"}
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error accepting quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to accept quote")


@router.post("/{quote_id}/decline", response_model=QuoteResponse)
async def decline_quote(request: Request, quote_id: str, body: DeclineQuoteRequest):
    """Mark quote as declined."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            quote = await conn.fetchrow("""
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'])

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote['status'] not in ('sent', 'viewed'):
                raise HTTPException(status_code=400, detail=f"Cannot decline quote with status '{quote['status']}'")

            await conn.execute("""
                UPDATE quotes SET status = 'declined', declined_at = NOW(), declined_reason = $3
                WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(quote_id), ctx['tenant_id'], body.reason)

            return QuoteResponse(
                success=True,
                message="Quote declined",
                data={"quote_number": quote['quote_number'], "status": "declined"}
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error declining quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to decline quote")


@router.post("/{quote_id}/duplicate", response_model=QuoteResponse)
async def duplicate_quote(request: Request, quote_id: str, body: DuplicateQuoteRequest = None):
    """Duplicate a quote."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get original quote
                quote = await conn.fetchrow("""
                    SELECT * FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """, uuid_module.UUID(quote_id), ctx['tenant_id'])

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                # Get original items
                items = await conn.fetch("""
                    SELECT * FROM quote_items WHERE quote_id = $1 ORDER BY sort_order
                """, uuid_module.UUID(quote_id))

                # Generate new number
                new_number = await conn.fetchval(
                    "SELECT generate_quote_number($1, 'QUO')",
                    ctx['tenant_id']
                )

                # Create new quote
                new_id = uuid_module.uuid4()
                new_date = body.quote_date if body and body.quote_date else date.today()
                new_expiry = body.expiry_date if body and body.expiry_date else quote['expiry_date']

                await conn.execute("""
                    INSERT INTO quotes (
                        id, tenant_id, quote_number, quote_date, expiry_date,
                        customer_id, customer_name, customer_email,
                        reference, subject,
                        subtotal, discount_type, discount_value, discount_amount,
                        tax_amount, total_amount, status,
                        notes, terms, footer, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, 'draft',
                        $17, $18, $19, $20
                    )
                """,
                    new_id, ctx['tenant_id'], new_number, new_date, new_expiry,
                    quote['customer_id'], quote['customer_name'], quote['customer_email'],
                    quote['reference'], quote['subject'],
                    quote['subtotal'], quote['discount_type'], quote['discount_value'], quote['discount_amount'],
                    quote['tax_amount'], quote['total_amount'],
                    quote['notes'], quote['terms'], quote['footer'], ctx['user_id']
                )

                # Copy items
                for item in items:
                    await conn.execute("""
                        INSERT INTO quote_items (
                            id, quote_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total,
                            group_name, sort_order
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                        )
                    """,
                        uuid_module.uuid4(), new_id,
                        item['item_id'], item['description'],
                        item['quantity'], item['unit'], item['unit_price'], item['discount_percent'],
                        item['tax_id'], item['tax_rate'], item['tax_amount'], item['line_total'],
                        item['group_name'], item['sort_order']
                    )

                return QuoteResponse(
                    success=True,
                    message="Quote duplicated successfully",
                    data={
                        "id": str(new_id),
                        "quote_number": new_number,
                        "original_quote_number": quote['quote_number']
                    }
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error duplicating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to duplicate quote")


# ============================================================================
# CONVERSION ENDPOINTS
# ============================================================================

@router.post("/{quote_id}/to-invoice", response_model=QuoteResponse)
async def convert_to_invoice(request: Request, quote_id: str, body: ConvertToInvoiceRequest = None):
    """Convert quote to invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get quote
                quote = await conn.fetchrow("""
                    SELECT * FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """, uuid_module.UUID(quote_id), ctx['tenant_id'])

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                if quote['status'] not in ('sent', 'accepted', 'viewed'):
                    raise HTTPException(status_code=400, detail=f"Cannot convert quote with status '{quote['status']}'")

                # Get items
                items_query = "SELECT * FROM quote_items WHERE quote_id = $1"
                if body and body.item_ids:
                    items_query += f" AND id = ANY($2)"
                    items = await conn.fetch(items_query, uuid_module.UUID(quote_id),
                        [uuid_module.UUID(id) for id in body.item_ids])
                else:
                    items = await conn.fetch(items_query, uuid_module.UUID(quote_id))

                if not items:
                    raise HTTPException(status_code=400, detail="No items to convert")

                # Generate invoice number
                invoice_number = await conn.fetchval(
                    "SELECT generate_invoice_number($1, 'INV')",
                    ctx['tenant_id']
                )

                # Create invoice
                invoice_id = uuid_module.uuid4()
                invoice_date = body.invoice_date if body and body.invoice_date else date.today()
                due_date = body.due_date if body and body.due_date else None

                # Recalculate totals for selected items
                subtotal = sum(item['line_total'] - item['tax_amount'] for item in items)
                tax_total = sum(item['tax_amount'] for item in items)
                total = subtotal + tax_total

                await conn.execute("""
                    INSERT INTO sales_invoices (
                        id, tenant_id, invoice_number, invoice_date, due_date,
                        customer_id, customer_name,
                        subtotal, tax_amount, total_amount,
                        status, quote_id, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7,
                        $8, $9, $10,
                        'draft', $11, $12
                    )
                """,
                    invoice_id, ctx['tenant_id'], invoice_number, invoice_date, due_date,
                    quote['customer_id'], quote['customer_name'],
                    subtotal, tax_total, total,
                    uuid_module.UUID(quote_id), ctx['user_id']
                )

                # Copy items to invoice_items
                for item in items:
                    await conn.execute("""
                        INSERT INTO sales_invoice_items (
                            id, invoice_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                        )
                    """,
                        uuid_module.uuid4(), invoice_id,
                        item['item_id'], item['description'],
                        item['quantity'], item['unit'], item['unit_price'], item['discount_percent'],
                        item['tax_id'], item['tax_rate'], item['tax_amount'], item['line_total']
                    )

                # Update quote status
                await conn.execute("""
                    UPDATE quotes SET
                        status = 'converted',
                        converted_to_type = 'invoice',
                        converted_to_id = $3,
                        converted_at = NOW()
                    WHERE id = $1 AND tenant_id = $2
                """, uuid_module.UUID(quote_id), ctx['tenant_id'], invoice_id)

                return QuoteResponse(
                    success=True,
                    message="Quote converted to invoice",
                    data={
                        "quote_id": quote_id,
                        "invoice_id": str(invoice_id),
                        "invoice_number": invoice_number
                    }
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting quote to invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to convert quote to invoice")


@router.post("/{quote_id}/to-order", response_model=QuoteResponse)
async def convert_to_sales_order(request: Request, quote_id: str, body: ConvertToOrderRequest = None):
    """Convert quote to sales order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get quote
                quote = await conn.fetchrow("""
                    SELECT * FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """, uuid_module.UUID(quote_id), ctx['tenant_id'])

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                if quote['status'] not in ('sent', 'accepted', 'viewed'):
                    raise HTTPException(status_code=400, detail=f"Cannot convert quote with status '{quote['status']}'")

                # Get items
                items_query = "SELECT * FROM quote_items WHERE quote_id = $1"
                if body and body.item_ids:
                    items_query += f" AND id = ANY($2)"
                    items = await conn.fetch(items_query, uuid_module.UUID(quote_id),
                        [uuid_module.UUID(id) for id in body.item_ids])
                else:
                    items = await conn.fetch(items_query, uuid_module.UUID(quote_id))

                if not items:
                    raise HTTPException(status_code=400, detail="No items to convert")

                # Generate SO number
                so_number = await conn.fetchval(
                    "SELECT generate_sales_order_number($1, 'SO')",
                    ctx['tenant_id']
                )

                # Create sales order
                so_id = uuid_module.uuid4()
                order_date = body.order_date if body and body.order_date else date.today()

                # Recalculate totals for selected items
                subtotal = sum(item['line_total'] - item['tax_amount'] for item in items)
                tax_total = sum(item['tax_amount'] for item in items)
                total = subtotal + tax_total

                await conn.execute("""
                    INSERT INTO sales_orders (
                        id, tenant_id, order_number, order_date, expected_ship_date,
                        customer_id, customer_name,
                        subtotal, tax_amount, total_amount,
                        status, quote_id, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7,
                        $8, $9, $10,
                        'draft', $11, $12
                    )
                """,
                    so_id, ctx['tenant_id'], so_number, order_date,
                    body.expected_ship_date if body else None,
                    quote['customer_id'], quote['customer_name'],
                    subtotal, tax_total, total,
                    uuid_module.UUID(quote_id), ctx['user_id']
                )

                # Copy items to sales_order_items
                for item in items:
                    await conn.execute("""
                        INSERT INTO sales_order_items (
                            id, sales_order_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                        )
                    """,
                        uuid_module.uuid4(), so_id,
                        item['item_id'], item['description'],
                        item['quantity'], item['unit'], item['unit_price'], item['discount_percent'],
                        item['tax_id'], item['tax_rate'], item['tax_amount'], item['line_total']
                    )

                # Update quote status
                await conn.execute("""
                    UPDATE quotes SET
                        status = 'converted',
                        converted_to_type = 'sales_order',
                        converted_to_id = $3,
                        converted_at = NOW()
                    WHERE id = $1 AND tenant_id = $2
                """, uuid_module.UUID(quote_id), ctx['tenant_id'], so_id)

                return QuoteResponse(
                    success=True,
                    message="Quote converted to sales order",
                    data={
                        "quote_id": quote_id,
                        "sales_order_id": str(so_id),
                        "order_number": so_number
                    }
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting quote to sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to convert quote to sales order")
