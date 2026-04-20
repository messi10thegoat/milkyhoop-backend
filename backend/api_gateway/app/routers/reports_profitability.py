"""
Profitability Report Router
Endpoints: /api/reports/profitability/items, /services, /reconciliation
"""
from datetime import date
from fastapi import APIRouter, HTTPException, Request, Query
from ..services.db_pool import get_db_pool
from ..services.profitability_query import get_item_profitability, get_service_revenue
from ..services.profitability_reconciliation import check_reconciliation
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports/profitability", tags=["reports-profitability"])


def _get_tenant_id(request: Request) -> str:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(401, detail="Not authenticated")
    tenant_id = request.state.user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(401, detail="No tenant context")
    return tenant_id


async def _set_rls(conn, tenant_id: str):
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)


def _parse_dates(start_date, end_date):
    today = date.today()
    if not start_date:
        start_date = today.replace(day=1).isoformat()
    if not end_date:
        end_date = today.isoformat()
    try:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, detail="Invalid date format. Use YYYY-MM-DD.")
    if ed < sd:
        raise HTTPException(400, detail="end_date must be >= start_date")
    if (ed - sd).days > 365:
        raise HTTPException(400, detail="Period cannot exceed 365 days")
    return sd, ed


@router.get("/items")
async def profitability_items(
    request: Request,
    start_date: str = Query(None),
    end_date: str = Query(None),
    sort_by: str = Query(
        "margin_desc",
        regex="^(margin_desc|margin_asc|revenue_desc|qty_desc|margin_pct_desc)$",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Realized gross margin per product (goods only)."""
    tenant_id = _get_tenant_id(request)
    sd, ed = _parse_dates(start_date, end_date)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await _set_rls(conn, tenant_id)
        items, summary, total_count = await get_item_profitability(
            conn,
            tenant_id,
            sd,
            ed,
            sort_by,
            limit,
            offset,
        )
        recon = await check_reconciliation(conn, tenant_id, sd, ed)

    if recon["should_block"]:
        raise HTTPException(
            500,
            detail={
                "error": "Report drift detected",
                "revenue_drift": recon["revenue_drift"],
                "cogs_drift": recon["cogs_drift"],
                "support_code": "PROFIT_RECON_FAIL",
            },
        )

    return {
        "success": True,
        "data": {
            "period": {
                "start_date": sd.isoformat(),
                "end_date": ed.isoformat(),
                "days": (ed - sd).days + 1,
            },
            "summary": summary,
            "items": items,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total_count,
            },
            "reconciliation": {
                "matches_pnl": recon["matches_pnl"],
                "severity": recon["severity"],
                "drift_amount": recon["revenue_drift"],
            },
        },
    }


@router.get("/services")
async def profitability_services(
    request: Request,
    start_date: str = Query(None),
    end_date: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Service item revenue (no COGS)."""
    tenant_id = _get_tenant_id(request)
    sd, ed = _parse_dates(start_date, end_date)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await _set_rls(conn, tenant_id)
        items, summary = await get_service_revenue(
            conn, tenant_id, sd, ed, limit, offset
        )

    return {
        "success": True,
        "data": {
            "period": {"start_date": sd.isoformat(), "end_date": ed.isoformat()},
            "summary": summary,
            "items": items,
        },
    }


@router.get("/reconciliation")
async def profitability_reconciliation(
    request: Request,
    start_date: str = Query(...),
    end_date: str = Query(...),
):
    """Health check: report vs P&L consistency."""
    tenant_id = _get_tenant_id(request)
    sd, ed = _parse_dates(start_date, end_date)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await _set_rls(conn, tenant_id)
        result = await check_reconciliation(conn, tenant_id, sd, ed)

    return {"success": True, "data": result}
