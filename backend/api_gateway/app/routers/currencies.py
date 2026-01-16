"""
Currencies and Exchange Rates Router
Multi-currency support with forex gain/loss tracking.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from datetime import date
from decimal import Decimal
import asyncpg
import logging
import uuid as uuid_module

from ..config import settings
from ..schemas.currencies import (
    CreateCurrencyRequest, UpdateCurrencyRequest,
    CreateExchangeRateRequest, ConvertAmountRequest,
    CurrencyListResponse, CurrencyResponse, CurrencyDetail, CurrencyListItem,
    ExchangeRateListResponse, ExchangeRateDetail, ExchangeRateResponse,
    LatestRatesResponse, LatestRateItem, ConvertAmountResponse,
    ForexReportResponse, RevaluationRequest
)

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10, command_timeout=60)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": uuid_module.UUID(user_id) if user_id else None}


# ============================================================================
# CURRENCY ENDPOINTS
# ============================================================================

@router.get("", response_model=CurrencyListResponse)
async def list_currencies(request: Request, include_inactive: bool = Query(False)):
    """List all currencies for tenant."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = "SELECT * FROM currencies WHERE tenant_id = $1"
            if not include_inactive:
                query += " AND is_active = true"
            query += " ORDER BY is_base_currency DESC, code ASC"

            rows = await conn.fetch(query, ctx['tenant_id'])

            return CurrencyListResponse(
                items=[CurrencyListItem(
                    id=str(r['id']),
                    code=r['code'],
                    name=r['name'],
                    symbol=r['symbol'],
                    decimal_places=r['decimal_places'],
                    is_base_currency=r['is_base_currency'],
                    is_active=r['is_active']
                ) for r in rows],
                total=len(rows)
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing currencies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list currencies")


@router.get("/{currency_id}")
async def get_currency(request: Request, currency_id: str):
    """Get currency detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM currencies WHERE id = $1 AND tenant_id = $2",
                uuid_module.UUID(currency_id), ctx['tenant_id']
            )
            if not row:
                raise HTTPException(status_code=404, detail="Currency not found")

            return {
                "success": True,
                "data": CurrencyDetail(
                    id=str(row['id']),
                    code=row['code'],
                    name=row['name'],
                    symbol=row['symbol'],
                    decimal_places=row['decimal_places'],
                    is_base_currency=row['is_base_currency'],
                    is_active=row['is_active'],
                    created_at=row['created_at'].isoformat(),
                    updated_at=row['updated_at'].isoformat()
                )
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting currency: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get currency")


@router.post("", response_model=CurrencyResponse)
async def create_currency(request: Request, body: CreateCurrencyRequest):
    """Create a new currency."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check for duplicate
            existing = await conn.fetchrow(
                "SELECT id FROM currencies WHERE tenant_id = $1 AND code = $2",
                ctx['tenant_id'], body.code
            )
            if existing:
                raise HTTPException(status_code=400, detail=f"Currency {body.code} already exists")

            currency_id = uuid_module.uuid4()
            await conn.execute("""
                INSERT INTO currencies (id, tenant_id, code, name, symbol, decimal_places, is_base_currency)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, currency_id, ctx['tenant_id'], body.code, body.name, body.symbol,
                body.decimal_places, body.is_base_currency)

            return CurrencyResponse(
                success=True,
                message=f"Currency {body.code} created",
                data={"id": str(currency_id), "code": body.code}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating currency: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create currency")


@router.patch("/{currency_id}", response_model=CurrencyResponse)
async def update_currency(request: Request, currency_id: str, body: UpdateCurrencyRequest):
    """Update a currency."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            currency = await conn.fetchrow(
                "SELECT id, code FROM currencies WHERE id = $1 AND tenant_id = $2",
                uuid_module.UUID(currency_id), ctx['tenant_id']
            )
            if not currency:
                raise HTTPException(status_code=404, detail="Currency not found")

            updates, params, idx = [], [], 1
            for field in ['name', 'symbol', 'decimal_places', 'is_active']:
                val = getattr(body, field, None)
                if val is not None:
                    updates.append(f"{field} = ${idx}")
                    params.append(val)
                    idx += 1

            if updates:
                params.extend([uuid_module.UUID(currency_id), ctx['tenant_id']])
                await conn.execute(
                    f"UPDATE currencies SET {', '.join(updates)} WHERE id = ${idx} AND tenant_id = ${idx+1}",
                    *params
                )

            return CurrencyResponse(success=True, message="Currency updated", data={"code": currency['code']})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating currency: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update currency")


@router.post("/{currency_id}/set-base", response_model=CurrencyResponse)
async def set_base_currency(request: Request, currency_id: str):
    """Set currency as base currency."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            currency = await conn.fetchrow(
                "SELECT id, code FROM currencies WHERE id = $1 AND tenant_id = $2",
                uuid_module.UUID(currency_id), ctx['tenant_id']
            )
            if not currency:
                raise HTTPException(status_code=404, detail="Currency not found")

            await conn.execute("""
                UPDATE currencies SET is_base_currency = true WHERE id = $1 AND tenant_id = $2
            """, uuid_module.UUID(currency_id), ctx['tenant_id'])

            return CurrencyResponse(
                success=True,
                message=f"{currency['code']} set as base currency",
                data={"code": currency['code']}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting base currency: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set base currency")


# ============================================================================
# EXCHANGE RATE ENDPOINTS
# ============================================================================

@router.get("/exchange-rates", response_model=ExchangeRateListResponse)
async def list_exchange_rates(
    request: Request,
    from_currency_id: Optional[str] = Query(None),
    to_currency_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100)
):
    """List exchange rates."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["er.tenant_id = $1"]
            params = [ctx['tenant_id']]
            idx = 2

            if from_currency_id:
                conditions.append(f"er.from_currency_id = ${idx}")
                params.append(uuid_module.UUID(from_currency_id))
                idx += 1
            if to_currency_id:
                conditions.append(f"er.to_currency_id = ${idx}")
                params.append(uuid_module.UUID(to_currency_id))
                idx += 1
            if start_date:
                conditions.append(f"er.rate_date >= ${idx}")
                params.append(start_date)
                idx += 1
            if end_date:
                conditions.append(f"er.rate_date <= ${idx}")
                params.append(end_date)
                idx += 1

            where_clause = " AND ".join(conditions)
            count = await conn.fetchval(f"SELECT COUNT(*) FROM exchange_rates er WHERE {where_clause}", *params)

            query = f"""
                SELECT er.*, cf.code as from_code, ct.code as to_code
                FROM exchange_rates er
                JOIN currencies cf ON er.from_currency_id = cf.id
                JOIN currencies ct ON er.to_currency_id = ct.id
                WHERE {where_clause}
                ORDER BY er.rate_date DESC
                LIMIT ${idx} OFFSET ${idx+1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            return ExchangeRateListResponse(
                items=[ExchangeRateDetail(
                    id=str(r['id']),
                    from_currency_id=str(r['from_currency_id']),
                    from_currency_code=r['from_code'],
                    to_currency_id=str(r['to_currency_id']),
                    to_currency_code=r['to_code'],
                    rate_date=r['rate_date'].isoformat(),
                    rate=float(r['rate']),
                    source=r['source'],
                    created_at=r['created_at'].isoformat()
                ) for r in rows],
                total=count,
                has_more=(skip + limit) < count
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing exchange rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list exchange rates")


@router.get("/exchange-rates/latest", response_model=LatestRatesResponse)
async def get_latest_rates(request: Request):
    """Get latest exchange rates for all currency pairs."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            base = await conn.fetchrow(
                "SELECT code FROM currencies WHERE tenant_id = $1 AND is_base_currency = true",
                ctx['tenant_id']
            )
            base_code = base['code'] if base else 'IDR'

            query = """
                SELECT DISTINCT ON (er.from_currency_id, er.to_currency_id)
                    cf.code as from_code, ct.code as to_code, er.rate, er.rate_date
                FROM exchange_rates er
                JOIN currencies cf ON er.from_currency_id = cf.id
                JOIN currencies ct ON er.to_currency_id = ct.id
                WHERE er.tenant_id = $1
                ORDER BY er.from_currency_id, er.to_currency_id, er.rate_date DESC
            """
            rows = await conn.fetch(query, ctx['tenant_id'])

            return LatestRatesResponse(
                success=True,
                base_currency=base_code,
                rates=[LatestRateItem(
                    from_currency_code=r['from_code'],
                    to_currency_code=r['to_code'],
                    rate=float(r['rate']),
                    rate_date=r['rate_date'].isoformat()
                ) for r in rows],
                as_of=date.today().isoformat()
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting latest rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get latest rates")


@router.post("/exchange-rates", response_model=ExchangeRateResponse)
async def create_exchange_rate(request: Request, body: CreateExchangeRateRequest):
    """Create a new exchange rate."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Validate currencies exist
            for cid in [body.from_currency_id, body.to_currency_id]:
                c = await conn.fetchrow(
                    "SELECT id FROM currencies WHERE id = $1 AND tenant_id = $2",
                    uuid_module.UUID(cid), ctx['tenant_id']
                )
                if not c:
                    raise HTTPException(status_code=400, detail=f"Currency {cid} not found")

            if body.from_currency_id == body.to_currency_id:
                raise HTTPException(status_code=400, detail="From and To currencies must be different")

            rate_id = uuid_module.uuid4()
            await conn.execute("""
                INSERT INTO exchange_rates (id, tenant_id, from_currency_id, to_currency_id, rate_date, rate, source, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (tenant_id, from_currency_id, to_currency_id, rate_date)
                DO UPDATE SET rate = $6, source = $7
            """, rate_id, ctx['tenant_id'],
                uuid_module.UUID(body.from_currency_id), uuid_module.UUID(body.to_currency_id),
                body.rate_date, body.rate, body.source, ctx['user_id'])

            return ExchangeRateResponse(
                success=True,
                message="Exchange rate saved",
                data={"id": str(rate_id), "rate": body.rate, "rate_date": body.rate_date.isoformat()}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating exchange rate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create exchange rate")


@router.get("/exchange-rates/convert", response_model=ConvertAmountResponse)
async def convert_amount(
    request: Request,
    amount: int = Query(..., description="Amount in smallest unit"),
    from_currency_id: str = Query(...),
    to_currency_id: str = Query(...),
    as_of_date: Optional[date] = Query(None)
):
    """Convert amount between currencies."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rate_date = as_of_date or date.today()

            # Get currency codes
            from_c = await conn.fetchrow("SELECT code FROM currencies WHERE id = $1", uuid_module.UUID(from_currency_id))
            to_c = await conn.fetchrow("SELECT code FROM currencies WHERE id = $1", uuid_module.UUID(to_currency_id))

            if not from_c or not to_c:
                raise HTTPException(status_code=400, detail="Invalid currency")

            # Get rate
            rate = await conn.fetchval("""
                SELECT get_exchange_rate($1, $2, $3, $4)
            """, ctx['tenant_id'], uuid_module.UUID(from_currency_id),
                uuid_module.UUID(to_currency_id), rate_date)

            if rate is None:
                raise HTTPException(status_code=400, detail="No exchange rate found")

            converted = int(Decimal(str(amount)) * Decimal(str(rate)))

            return ConvertAmountResponse(
                success=True,
                original_amount=amount,
                converted_amount=converted,
                from_currency_code=from_c['code'],
                to_currency_code=to_c['code'],
                rate=float(rate),
                rate_date=rate_date.isoformat()
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting amount: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to convert amount")


# ============================================================================
# FOREX GAIN/LOSS ENDPOINTS
# ============================================================================

@router.get("/forex/gain-loss", response_model=ForexReportResponse)
async def get_forex_gain_loss_report(
    request: Request,
    start_date: date = Query(...),
    end_date: date = Query(...)
):
    """Get forex gain/loss report."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT fgl.*, c.code as currency_code
                FROM forex_gain_loss fgl
                JOIN currencies c ON fgl.original_currency_id = c.id
                WHERE fgl.tenant_id = $1
                AND fgl.transaction_date BETWEEN $2 AND $3
                ORDER BY fgl.transaction_date DESC
            """
            rows = await conn.fetch(query, ctx['tenant_id'], start_date, end_date)

            items = []
            realized_gain, realized_loss = 0, 0
            unrealized_gain, unrealized_loss = 0, 0

            for r in rows:
                is_gain = r['gain_loss_amount'] > 0
                items.append({
                    "id": str(r['id']),
                    "source_type": r['source_type'],
                    "source_id": str(r['source_id']) if r['source_id'] else None,
                    "transaction_date": r['transaction_date'].isoformat(),
                    "original_currency_code": r['currency_code'],
                    "original_amount": r['original_amount'],
                    "original_rate": float(r['original_rate']),
                    "settlement_rate": float(r['settlement_rate']),
                    "gain_loss_amount": r['gain_loss_amount'],
                    "is_gain": is_gain,
                    "is_realized": r['is_realized'],
                    "journal_id": str(r['journal_id']) if r['journal_id'] else None,
                    "created_at": r['created_at'].isoformat()
                })

                if r['is_realized']:
                    if is_gain:
                        realized_gain += r['gain_loss_amount']
                    else:
                        realized_loss += abs(r['gain_loss_amount'])
                else:
                    if is_gain:
                        unrealized_gain += r['gain_loss_amount']
                    else:
                        unrealized_loss += abs(r['gain_loss_amount'])

            return ForexReportResponse(
                success=True,
                data={
                    "period_start": start_date.isoformat(),
                    "period_end": end_date.isoformat(),
                    "realized_gain": realized_gain,
                    "realized_loss": realized_loss,
                    "net_realized": realized_gain - realized_loss,
                    "unrealized_gain": unrealized_gain,
                    "unrealized_loss": unrealized_loss,
                    "net_unrealized": unrealized_gain - unrealized_loss,
                    "items": items
                }
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting forex report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get forex report")
