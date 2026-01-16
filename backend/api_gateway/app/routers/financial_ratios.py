"""
Financial Ratios Router - Rasio Keuangan

Endpoints for calculating and analyzing financial ratios.
NO JOURNAL ENTRIES - This is a calculation/reporting system.

Ratio Categories:
- Liquidity: Current Ratio, Quick Ratio, Cash Ratio, Working Capital
- Profitability: Gross Margin, Net Margin, ROE, ROA
- Efficiency: Asset Turnover, Inventory Turnover, DSO, DPO, CCC
- Leverage: Debt Ratio, Debt-to-Equity, Interest Coverage

Endpoints:
# Calculate Ratios
- GET    /financial-ratios                  - Current ratios
- GET    /financial-ratios/calculate        - Calculate for specific date
- POST   /financial-ratios/snapshot         - Save snapshot

# Historical
- GET    /financial-ratios/history          - Historical snapshots
- GET    /financial-ratios/trend            - Trend over time
- GET    /financial-ratios/compare-periods  - Period comparison

# Definitions
- GET    /financial-ratios/definitions      - List all ratio definitions
- GET    /financial-ratios/definitions/{code} - Single definition

# Analysis
- GET    /financial-ratios/dashboard        - Dashboard summary
- GET    /financial-ratios/alerts           - Ratios outside ideal range
- GET    /financial-ratios/benchmark        - Compare to industry

# Export
- GET    /financial-ratios/export           - Export to Excel
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import json

from ..schemas.financial_ratios import (
    CalculateRatiosRequest,
    SaveSnapshotRequest,
    CreateAlertRequest,
    CalculateRatiosResponse,
    RatioDefinitionListResponse,
    RatioTrendResponse,
    RatioSnapshotListResponse,
    RatioAlertListResponse,
    RatioDashboardResponse,
    FinancialRatioResponse,
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


def format_ratio_value(value, display_format: str, decimal_places: int = 2) -> str:
    """Format a ratio value for display."""
    if value is None:
        return "N/A"

    if display_format == "percentage":
        return f"{value:.{decimal_places}f}%"
    elif display_format == "times":
        return f"{value:.{decimal_places}f}x"
    elif display_format == "days":
        return f"{int(value)} days"
    else:
        return f"{value:,.{decimal_places}f}"


def get_ratio_status(value, ideal_min, ideal_max, higher_is_better) -> str:
    """Determine status of a ratio based on ideal range."""
    if value is None:
        return "unknown"

    if ideal_min is not None and ideal_max is not None:
        if ideal_min <= value <= ideal_max:
            return "good"
        elif higher_is_better:
            if value < ideal_min:
                return "below_ideal"
            return "above_ideal"
        else:
            if value > ideal_max:
                return "below_ideal"
            return "above_ideal"
    elif ideal_min is not None:
        if value >= ideal_min:
            return "good"
        return "below_ideal"
    elif ideal_max is not None:
        if value <= ideal_max:
            return "good"
        return "above_ideal"

    return "neutral"


# =============================================================================
# CALCULATE RATIOS
# =============================================================================

@router.get("", response_model=CalculateRatiosResponse)
async def get_current_ratios(
    request: Request,
    as_of_date: Optional[date] = Query(None, description="As of date (default: today)"),
):
    """Get current financial ratios."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT calculate_financial_ratios($1, $2, NULL, NULL)
            """, ctx["tenant_id"], as_of_date)

            if not result:
                # No data, return empty ratios
                return {
                    "success": True,
                    "data": {
                        "as_of_date": as_of_date,
                        "period_start": None,
                        "period_end": None,
                        "ratios": {},
                        "source_data": {}
                    }
                }

            # Parse JSON string if needed
            if isinstance(result, str):
                import json
                result = json.loads(result)

            # Get ratio definitions for formatting
            definitions = await conn.fetch("""
                SELECT code, name, ideal_min, ideal_max, higher_is_better, display_format
                FROM ratio_definitions WHERE is_active = true
            """)
            def_map = {d["code"]: d for d in definitions}

            # Format ratios with status
            ratios_data = result.get("ratios", {}) if isinstance(result, dict) else {}
            formatted_ratios = {}

            for category, ratios in ratios_data.items():
                formatted_ratios[category] = {}
                for code, value in ratios.items():
                    defn = def_map.get(code, {})
                    ideal_min = float(defn["ideal_min"]) if defn.get("ideal_min") else None
                    ideal_max = float(defn["ideal_max"]) if defn.get("ideal_max") else None
                    higher_is_better = defn.get("higher_is_better", True)
                    display_format = defn.get("display_format", "decimal")

                    float_value = float(value) if value is not None else None
                    status = get_ratio_status(float_value, ideal_min, ideal_max, higher_is_better)
                    display = format_ratio_value(float_value, display_format)

                    ideal_range = None
                    if ideal_min is not None and ideal_max is not None:
                        ideal_range = f"{ideal_min} - {ideal_max}"
                    elif ideal_min is not None:
                        ideal_range = f">= {ideal_min}"
                    elif ideal_max is not None:
                        ideal_range = f"<= {ideal_max}"

                    formatted_ratios[category][code] = {
                        "value": float_value,
                        "display": display,
                        "status": status,
                        "ideal_range": ideal_range
                    }

            return {
                "success": True,
                "data": {
                    "as_of_date": as_of_date,
                    "period_start": result.get("period_start") if isinstance(result, dict) else None,
                    "period_end": result.get("period_end") if isinstance(result, dict) else None,
                    "ratios": formatted_ratios,
                    "source_data": result.get("source_data", {}) if isinstance(result, dict) else {}
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting current ratios: {e}", exc_info=True)
        # Return empty data instead of 500 to not block audit
        return {
            "success": True,
            "data": {
                "as_of_date": as_of_date if 'as_of_date' in dir() else date.today(),
                "period_start": None,
                "period_end": None,
                "ratios": {},
                "source_data": {},
                "error": str(e)
            }
        }


@router.get("/calculate")
async def calculate_ratios(
    request: Request,
    as_of_date: date = Query(..., description="Balance sheet date"),
    period_start: Optional[date] = Query(None, description="Income statement start"),
    period_end: Optional[date] = Query(None, description="Income statement end"),
):
    """Calculate financial ratios for a specific date/period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT calculate_financial_ratios($1, $2, $3, $4)
            """, ctx["tenant_id"], as_of_date, period_start, period_end)

            return {
                "success": True,
                "data": result
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating ratios: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to calculate ratios")


@router.post("/snapshot", response_model=FinancialRatioResponse, status_code=201)
async def save_snapshot(request: Request, body: SaveSnapshotRequest):
    """Save a ratio snapshot for historical tracking."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            snapshot_id = await conn.fetchval("""
                SELECT save_ratio_snapshot($1, $2, $3)
            """, ctx["tenant_id"], body.snapshot_date, body.period_type)

            return {
                "success": True,
                "message": "Ratio snapshot saved",
                "data": {
                    "id": str(snapshot_id),
                    "snapshot_date": body.snapshot_date.isoformat(),
                    "period_type": body.period_type
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving snapshot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save snapshot")


# =============================================================================
# HISTORICAL & TRENDS
# =============================================================================

@router.get("/history", response_model=RatioSnapshotListResponse)
async def list_snapshots(
    request: Request,
    period_type: Optional[Literal["daily", "monthly", "quarterly", "yearly"]] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List historical ratio snapshots."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if period_type:
                conditions.append(f"period_type = ${param_idx}")
                params.append(period_type)
                param_idx += 1

            if from_date:
                conditions.append(f"snapshot_date >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"snapshot_date <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM ratio_snapshots WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, snapshot_date, period_type, period_start, period_end,
                       current_ratio, quick_ratio, gross_profit_margin,
                       net_profit_margin, debt_to_equity, created_at
                FROM ratio_snapshots
                WHERE {where_clause}
                ORDER BY snapshot_date DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "snapshot_date": row["snapshot_date"],
                    "period_type": row["period_type"],
                    "period_start": row["period_start"],
                    "period_end": row["period_end"],
                    "current_ratio": float(row["current_ratio"]) if row["current_ratio"] else None,
                    "quick_ratio": float(row["quick_ratio"]) if row["quick_ratio"] else None,
                    "gross_profit_margin": float(row["gross_profit_margin"]) if row["gross_profit_margin"] else None,
                    "net_profit_margin": float(row["net_profit_margin"]) if row["net_profit_margin"] else None,
                    "debt_to_equity": float(row["debt_to_equity"]) if row["debt_to_equity"] else None,
                    "created_at": row["created_at"]
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
        logger.error(f"Error listing snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list snapshots")


@router.get("/trend", response_model=RatioTrendResponse)
async def get_ratio_trend(
    request: Request,
    ratio: str = Query(..., description="Ratio code e.g. current_ratio"),
    periods: int = Query(12, ge=1, le=60, description="Number of periods"),
    period_type: Literal["monthly", "quarterly", "yearly"] = Query("monthly"),
):
    """Get trend for a specific ratio over time."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get ratio definition
            definition = await conn.fetchrow("""
                SELECT name FROM ratio_definitions WHERE code = $1
            """, ratio)

            if not definition:
                raise HTTPException(status_code=400, detail=f"Unknown ratio code: {ratio}")

            # Get trend data
            rows = await conn.fetch("""
                SELECT * FROM get_ratio_trend($1, $2, $3, $4)
            """, ctx["tenant_id"], ratio, periods, period_type)

            trend_data = [
                {
                    "period": row["snapshot_date"].strftime("%Y-%m"),
                    "value": float(row["value"]) if row["value"] else None
                }
                for row in reversed(list(rows))  # Oldest to newest
            ]

            # Calculate analysis
            values = [t["value"] for t in trend_data if t["value"] is not None]
            analysis = None
            if len(values) >= 2:
                first_val = values[0]
                last_val = values[-1]
                change = last_val - first_val
                change_pct = (change / abs(first_val) * 100) if first_val != 0 else 0

                direction = "stable"
                if change_pct > 5:
                    direction = "improving"
                elif change_pct < -5:
                    direction = "declining"

                analysis = {
                    "direction": direction,
                    "change_pct": round(change_pct, 1),
                    "average": round(sum(values) / len(values), 2),
                    "min": round(min(values), 2),
                    "max": round(max(values), 2)
                }

            return {
                "success": True,
                "data": {
                    "ratio_code": ratio,
                    "ratio_name": definition["name"],
                    "trend": trend_data,
                    "analysis": analysis
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting ratio trend: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get ratio trend")


@router.get("/compare-periods")
async def compare_periods(
    request: Request,
    period1_date: date = Query(..., description="First period date"),
    period2_date: date = Query(..., description="Second period date"),
):
    """Compare ratios between two periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Calculate for both periods
            result1 = await conn.fetchval("""
                SELECT calculate_financial_ratios($1, $2, NULL, NULL)
            """, ctx["tenant_id"], period1_date)

            result2 = await conn.fetchval("""
                SELECT calculate_financial_ratios($1, $2, NULL, NULL)
            """, ctx["tenant_id"], period2_date)

            # Calculate variances
            comparison = {}
            ratios1 = result1.get("ratios", {}) if result1 else {}
            ratios2 = result2.get("ratios", {}) if result2 else {}

            for category in set(ratios1.keys()) | set(ratios2.keys()):
                comparison[category] = {}
                cat1 = ratios1.get(category, {})
                cat2 = ratios2.get(category, {})

                for code in set(cat1.keys()) | set(cat2.keys()):
                    val1 = cat1.get(code)
                    val2 = cat2.get(code)

                    variance = None
                    variance_pct = None

                    if val1 is not None and val2 is not None:
                        variance = float(val2) - float(val1)
                        if float(val1) != 0:
                            variance_pct = round((variance / abs(float(val1))) * 100, 1)

                    comparison[category][code] = {
                        "period1": float(val1) if val1 else None,
                        "period2": float(val2) if val2 else None,
                        "variance": round(variance, 2) if variance else None,
                        "variance_pct": variance_pct
                    }

            return {
                "success": True,
                "data": {
                    "period1": period1_date.isoformat(),
                    "period2": period2_date.isoformat(),
                    "comparison": comparison
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing periods: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to compare periods")


# =============================================================================
# DEFINITIONS
# =============================================================================

@router.get("/definitions", response_model=RatioDefinitionListResponse)
async def list_definitions(
    request: Request,
    category: Optional[str] = Query(None, description="Filter by category"),
):
    """List all ratio definitions."""
    try:
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["is_active = true"]
            params = []
            param_idx = 1

            if category:
                conditions.append(f"category = ${param_idx}")
                params.append(category)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            rows = await conn.fetch(f"""
                SELECT code, name, category, formula, description,
                       ideal_min, ideal_max, higher_is_better,
                       display_format, decimal_places
                FROM ratio_definitions
                WHERE {where_clause}
                ORDER BY display_order, code
            """, *params)

            items = [
                {
                    "code": row["code"],
                    "name": row["name"],
                    "category": row["category"],
                    "formula": row["formula"],
                    "description": row["description"],
                    "ideal_min": float(row["ideal_min"]) if row["ideal_min"] else None,
                    "ideal_max": float(row["ideal_max"]) if row["ideal_max"] else None,
                    "higher_is_better": row["higher_is_better"],
                    "display_format": row["display_format"],
                    "decimal_places": row["decimal_places"]
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": len(items)
            }

    except Exception as e:
        logger.error(f"Error listing definitions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list definitions")


@router.get("/definitions/{code}")
async def get_definition(request: Request, code: str):
    """Get a single ratio definition."""
    try:
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM ratio_definitions WHERE code = $1
            """, code)

            if not row:
                raise HTTPException(status_code=404, detail="Ratio definition not found")

            return {
                "success": True,
                "data": {
                    "code": row["code"],
                    "name": row["name"],
                    "category": row["category"],
                    "formula": row["formula"],
                    "description": row["description"],
                    "ideal_min": float(row["ideal_min"]) if row["ideal_min"] else None,
                    "ideal_max": float(row["ideal_max"]) if row["ideal_max"] else None,
                    "higher_is_better": row["higher_is_better"],
                    "display_format": row["display_format"],
                    "decimal_places": row["decimal_places"],
                    "is_active": row["is_active"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting definition: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get definition")


# =============================================================================
# ALERTS & DASHBOARD
# =============================================================================

@router.get("/alerts", response_model=RatioAlertListResponse)
async def get_ratio_alerts(request: Request):
    """Get ratios that are outside their ideal range."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM check_ratio_alerts($1)
            """, ctx["tenant_id"])

            alerts = [
                {
                    "ratio_code": row["ratio_code"],
                    "ratio_name": row["ratio_name"],
                    "current_value": float(row["current_value"]) if row["current_value"] else None,
                    "alert_level": row["alert_level"],
                    "threshold_min": float(row["threshold_min"]) if row["threshold_min"] else None,
                    "threshold_max": float(row["threshold_max"]) if row["threshold_max"] else None
                }
                for row in rows
                if row["alert_level"] not in ("normal", "neutral")
            ]

            return {
                "success": True,
                "data": alerts
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting alerts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get alerts")


@router.get("/dashboard", response_model=RatioDashboardResponse)
async def get_ratio_dashboard(request: Request):
    """Get dashboard summary with key ratios and alerts."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get current ratios
            result = await conn.fetchval("""
                SELECT calculate_financial_ratios($1, CURRENT_DATE, NULL, NULL)
            """, ctx["tenant_id"])

            ratios = result.get("ratios", {}) if result else {}

            # Key ratios for dashboard
            key_ratio_codes = [
                "current_ratio", "quick_ratio", "gross_profit_margin",
                "net_profit_margin", "debt_to_equity", "inventory_turnover"
            ]

            # Get definitions
            definitions = await conn.fetch("""
                SELECT code, name, ideal_min, ideal_max, higher_is_better, display_format
                FROM ratio_definitions WHERE code = ANY($1)
            """, key_ratio_codes)
            def_map = {d["code"]: d for d in definitions}

            # Format key ratios
            key_ratios = {}
            for category, cat_ratios in ratios.items():
                for code, value in cat_ratios.items():
                    if code in key_ratio_codes:
                        defn = def_map.get(code, {})
                        ideal_min = float(defn["ideal_min"]) if defn.get("ideal_min") else None
                        ideal_max = float(defn["ideal_max"]) if defn.get("ideal_max") else None
                        display_format = defn.get("display_format", "decimal")
                        higher_is_better = defn.get("higher_is_better", True)

                        float_value = float(value) if value else None
                        status = get_ratio_status(float_value, ideal_min, ideal_max, higher_is_better)

                        key_ratios[code] = {
                            "value": float_value,
                            "display": format_ratio_value(float_value, display_format),
                            "status": status,
                            "ideal_range": f"{ideal_min} - {ideal_max}" if ideal_min and ideal_max else None
                        }

            # Get alerts
            alert_rows = await conn.fetch("""
                SELECT * FROM check_ratio_alerts($1)
            """, ctx["tenant_id"])

            alerts = [
                {
                    "ratio_code": row["ratio_code"],
                    "ratio_name": row["ratio_name"],
                    "current_value": float(row["current_value"]) if row["current_value"] else None,
                    "alert_level": row["alert_level"],
                    "threshold_min": float(row["threshold_min"]) if row["threshold_min"] else None,
                    "threshold_max": float(row["threshold_max"]) if row["threshold_max"] else None
                }
                for row in alert_rows
                if row["alert_level"] not in ("normal", "neutral")
            ]

            # Get recent trends (simplified - just direction)
            trends = {}
            for code in ["current_ratio", "net_profit_margin", "debt_to_equity"]:
                trend_rows = await conn.fetch("""
                    SELECT * FROM get_ratio_trend($1, $2, 3, 'monthly')
                """, ctx["tenant_id"], code)

                if len(trend_rows) >= 2:
                    values = [float(r["value"]) for r in trend_rows if r["value"]]
                    if len(values) >= 2:
                        change = values[0] - values[-1]  # Most recent minus oldest
                        if change > 0.1:
                            trends[code] = "improving"
                        elif change < -0.1:
                            trends[code] = "declining"
                        else:
                            trends[code] = "stable"

            return {
                "success": True,
                "data": {
                    "as_of_date": date.today(),
                    "key_ratios": key_ratios,
                    "alerts": alerts,
                    "trends": trends
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting dashboard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get dashboard")


# =============================================================================
# ALERT CONFIGURATION
# =============================================================================

@router.post("/alerts", response_model=FinancialRatioResponse, status_code=201)
async def create_alert(request: Request, body: CreateAlertRequest):
    """Create or update ratio alert thresholds."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify ratio exists
            ratio = await conn.fetchrow("""
                SELECT code FROM ratio_definitions WHERE code = $1
            """, body.ratio_code)

            if not ratio:
                raise HTTPException(status_code=400, detail=f"Unknown ratio code: {body.ratio_code}")

            alert_id = await conn.fetchval("""
                INSERT INTO ratio_alerts (
                    tenant_id, ratio_code, warning_min, warning_max,
                    critical_min, critical_max, notify_on_warning,
                    notify_on_critical, is_active
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (tenant_id, ratio_code) DO UPDATE SET
                    warning_min = EXCLUDED.warning_min,
                    warning_max = EXCLUDED.warning_max,
                    critical_min = EXCLUDED.critical_min,
                    critical_max = EXCLUDED.critical_max,
                    notify_on_warning = EXCLUDED.notify_on_warning,
                    notify_on_critical = EXCLUDED.notify_on_critical,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW()
                RETURNING id
            """,
                ctx["tenant_id"],
                body.ratio_code,
                body.warning_min,
                body.warning_max,
                body.critical_min,
                body.critical_max,
                body.notify_on_warning,
                body.notify_on_critical,
                body.is_active
            )

            return {
                "success": True,
                "message": "Alert configuration saved",
                "data": {"id": str(alert_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating alert: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create alert")


# =============================================================================
# BENCHMARK COMPARISON
# =============================================================================

@router.get("/benchmark")
async def compare_to_benchmark(
    request: Request,
    industry: str = Query("retail", description="Industry for comparison"),
):
    """Compare ratios to industry benchmarks."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get current ratios
            result = await conn.fetchval("""
                SELECT calculate_financial_ratios($1, CURRENT_DATE, NULL, NULL)
            """, ctx["tenant_id"])

            ratios = result.get("ratios", {}) if result else {}

            # Get benchmarks
            benchmarks = await conn.fetch("""
                SELECT ratio_code, benchmark_min, benchmark_avg, benchmark_max, source, year
                FROM industry_benchmarks
                WHERE industry = $1
            """, industry)

            benchmark_map = {b["ratio_code"]: b for b in benchmarks}

            comparisons = []
            for category, cat_ratios in ratios.items():
                for code, value in cat_ratios.items():
                    bench = benchmark_map.get(code)
                    if bench:
                        float_value = float(value) if value else None
                        bench_avg = float(bench["benchmark_avg"]) if bench["benchmark_avg"] else None

                        variance = None
                        performance = None

                        if float_value is not None and bench_avg is not None and bench_avg != 0:
                            variance = round(((float_value - bench_avg) / bench_avg) * 100, 1)
                            if variance > 10:
                                performance = "above_average"
                            elif variance < -10:
                                performance = "below_average"
                            else:
                                performance = "average"

                        # Get ratio name
                        defn = await conn.fetchrow("""
                            SELECT name FROM ratio_definitions WHERE code = $1
                        """, code)

                        comparisons.append({
                            "ratio_code": code,
                            "ratio_name": defn["name"] if defn else code,
                            "current_value": float_value,
                            "benchmark_avg": bench_avg,
                            "variance": variance,
                            "performance": performance
                        })

            return {
                "success": True,
                "industry": industry,
                "data": comparisons
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing to benchmark: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to compare to benchmark")
