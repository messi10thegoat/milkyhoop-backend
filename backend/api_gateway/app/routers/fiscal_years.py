"""
Fiscal Years Router - Fiscal Year Management

CRUD endpoints for managing fiscal years and auto-creating periods.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg

from ..schemas.fiscal_years import (
    CreateFiscalYearRequest,
    FiscalYearResponse,
    FiscalYearListItem,
    FiscalYearListResponse,
    PeriodSummary,
)
from ..config import settings

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
# LIST FISCAL YEARS
# =============================================================================
@router.get("", response_model=FiscalYearListResponse)
async def list_fiscal_years(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status: open, closed"),
):
    """List all fiscal years for the tenant."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["fy.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status:
                conditions.append(f"fy.status = ${param_idx}")
                params.append(status)

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT
                    fy.id, fy.name, fy.start_date, fy.end_date, fy.status, fy.created_at,
                    COUNT(fp.id) as period_count,
                    COUNT(fp.id) FILTER (WHERE fp.status = 'OPEN') as open_period_count,
                    COUNT(fp.id) FILTER (WHERE fp.status IN ('CLOSED', 'LOCKED')) as closed_period_count
                FROM fiscal_years fy
                LEFT JOIN fiscal_periods fp ON fp.fiscal_year_id = fy.id
                WHERE {where_clause}
                GROUP BY fy.id
                ORDER BY fy.start_date DESC
            """

            rows = await conn.fetch(query, *params)

            items = [
                FiscalYearListItem(
                    id=str(row["id"]),
                    name=row["name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    period_count=row["period_count"] or 0,
                    open_period_count=row["open_period_count"] or 0,
                    closed_period_count=row["closed_period_count"] or 0,
                    created_at=row["created_at"],
                )
                for row in rows
            ]

            return FiscalYearListResponse(data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List fiscal years error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list fiscal years")


# =============================================================================
# GET FISCAL YEAR DETAIL
# =============================================================================
@router.get("/{fiscal_year_id}", response_model=dict)
async def get_fiscal_year(request: Request, fiscal_year_id: UUID):
    """Get fiscal year detail with all periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get fiscal year
            fy_row = await conn.fetchrow(
                """
                SELECT id, name, start_month, start_date, end_date, status,
                       closed_at, closed_by, created_at
                FROM fiscal_years
                WHERE id = $1 AND tenant_id = $2
            """,
                fiscal_year_id,
                ctx["tenant_id"],
            )

            if not fy_row:
                raise HTTPException(status_code=404, detail="Fiscal year not found")

            # Get periods
            period_rows = await conn.fetch(
                """
                SELECT id, period_number, period_name, start_date, end_date, status
                FROM fiscal_periods
                WHERE fiscal_year_id = $1 AND tenant_id = $2
                ORDER BY period_number
            """,
                fiscal_year_id,
                ctx["tenant_id"],
            )

            periods = [
                PeriodSummary(
                    id=str(row["id"]),
                    period_number=row["period_number"] or 0,
                    period_name=row["period_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                )
                for row in period_rows
            ]

            return {
                "success": True,
                "data": FiscalYearResponse(
                    id=str(fy_row["id"]),
                    name=fy_row["name"],
                    start_month=fy_row["start_month"],
                    start_date=fy_row["start_date"],
                    end_date=fy_row["end_date"],
                    status=fy_row["status"],
                    periods=periods,
                    closed_at=fy_row["closed_at"],
                    closed_by=str(fy_row["closed_by"]) if fy_row["closed_by"] else None,
                    created_at=fy_row["created_at"],
                ),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get fiscal year error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get fiscal year")


# =============================================================================
# CREATE FISCAL YEAR
# =============================================================================
@router.post("", response_model=dict, status_code=201)
async def create_fiscal_year(request: Request, body: CreateFiscalYearRequest):
    """Create a new fiscal year with 12 monthly periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Use the helper function to create fiscal year with periods
            try:
                fiscal_year_id = await conn.fetchval(
                    """
                    SELECT create_fiscal_year_with_periods($1, $2, $3, $4, $5)
                """,
                    ctx["tenant_id"],
                    body.name,
                    body.start_month,
                    body.year,
                    ctx["user_id"],
                )
            except asyncpg.RaiseError as e:
                if "overlaps" in str(e).lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Fiscal year overlaps with existing year",
                    )
                raise

            # Fetch the created fiscal year
            return await get_fiscal_year(request, fiscal_year_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create fiscal year error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create fiscal year")


# =============================================================================
# CLOSE FISCAL YEAR
# =============================================================================
@router.post("/{fiscal_year_id}/close", response_model=dict)
async def close_fiscal_year(request: Request, fiscal_year_id: UUID):
    """Close entire fiscal year (all periods must be closed first)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check fiscal year exists
            fy_row = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_years
                WHERE id = $1 AND tenant_id = $2
            """,
                fiscal_year_id,
                ctx["tenant_id"],
            )

            if not fy_row:
                raise HTTPException(status_code=404, detail="Fiscal year not found")

            if fy_row["status"] == "closed":
                raise HTTPException(
                    status_code=409, detail="Fiscal year is already closed"
                )

            # Check all periods are closed
            open_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM fiscal_periods
                WHERE fiscal_year_id = $1 AND tenant_id = $2 AND status NOT IN ('CLOSED', 'LOCKED')
            """,
                fiscal_year_id,
                ctx["tenant_id"],
            )

            if open_count > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot close fiscal year: {open_count} period(s) are still open",
                )

            # Close fiscal year
            await conn.execute(
                """
                UPDATE fiscal_years
                SET status = 'closed', closed_at = NOW(), closed_by = $3
                WHERE id = $1 AND tenant_id = $2
            """,
                fiscal_year_id,
                ctx["tenant_id"],
                ctx["user_id"],
            )

            return await get_fiscal_year(request, fiscal_year_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Close fiscal year error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close fiscal year")
