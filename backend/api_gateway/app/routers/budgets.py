"""
Budgets Router
==============
Budget planning and variance analysis.
NO journal entries - budgets are planning data only.
"""
from datetime import date
from typing import Optional, List
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.budgets import (
    BudgetCreate,
    BudgetUpdate,
    BudgetResponse,
    BudgetDetailResponse,
    BudgetListResponse,
    BudgetItemCreate,
    BudgetItemResponse,
    BudgetVsActualItem,
    BudgetVsActualResponse,
    BudgetVsActualMonthlyItem,
    BudgetVsActualMonthlyResponse,
    VarianceAlertItem,
    VarianceAlertsResponse,
    BudgetSummaryByType,
    BudgetSummaryResponse,
    BudgetByCostCenterItem,
    BudgetByCostCenterResponse,
    BudgetRevisionResponse,
    BudgetItemsImportRequest,
    BudgetItemsImportResponse,
    BudgetStatus,
)

router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


# ============================================================================
# BUDGET CRUD
# ============================================================================

@router.get("", response_model=BudgetListResponse)
async def list_budgets(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    fiscal_year: Optional[int] = None,
    status: Optional[BudgetStatus] = None,
):
    """List budgets"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if fiscal_year:
            where_clauses.append(f"fiscal_year = ${param_idx}")
            params.append(fiscal_year)
            param_idx += 1

        if status:
            where_clauses.append(f"status = ${param_idx}")
            params.append(status.value)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(f"SELECT COUNT(*) FROM budgets WHERE {where_sql}", *params)

        rows = await conn.fetch(
            f"""
            SELECT * FROM budgets
            WHERE {where_sql}
            ORDER BY fiscal_year DESC, name
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params, skip, limit
        )

        items = [BudgetResponse(**dict(row)) for row in rows]
        return BudgetListResponse(items=items, total=total)


@router.get("/{budget_id}", response_model=BudgetDetailResponse)
async def get_budget(request: Request, budget_id: UUID):
    """Get budget with items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        budget = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")

        items = await conn.fetch(
            """
            SELECT bi.*, coa.account_code, coa.name as account_name,
                   cc.code as cost_center_code, cc.name as cost_center_name
            FROM budget_items bi
            JOIN chart_of_accounts coa ON bi.account_id = coa.id
            LEFT JOIN cost_centers cc ON bi.cost_center_id = cc.id
            WHERE bi.budget_id = $1
            ORDER BY coa.account_code
            """,
            budget_id
        )

        item_responses = [BudgetItemResponse(**dict(item)) for item in items]
        total_budget = sum(i.annual_amount for i in item_responses)

        return BudgetDetailResponse(
            **dict(budget),
            items=item_responses,
            total_budget=total_budget
        )


@router.post("", response_model=BudgetResponse, status_code=201)
async def create_budget(request: Request, data: BudgetCreate):
    """Create budget"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Check uniqueness
        exists = await conn.fetchval(
            "SELECT 1 FROM budgets WHERE tenant_id = $1 AND fiscal_year = $2 AND name = $3",
            ctx["tenant_id"], data.fiscal_year, data.name
        )
        if exists:
            raise HTTPException(status_code=400, detail="Budget with this name already exists for the fiscal year")

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO budgets (tenant_id, name, description, fiscal_year, budget_type, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                ctx["tenant_id"], data.name, data.description, data.fiscal_year,
                data.budget_type.value, ctx.get("user_id")
            )

            # Insert items if provided
            if data.items:
                for item in data.items:
                    await conn.execute(
                        """
                        INSERT INTO budget_items (
                            budget_id, account_id, cost_center_id,
                            jan_amount, feb_amount, mar_amount, apr_amount,
                            may_amount, jun_amount, jul_amount, aug_amount,
                            sep_amount, oct_amount, nov_amount, dec_amount, notes
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                        """,
                        row["id"], item.account_id, item.cost_center_id,
                        item.jan_amount, item.feb_amount, item.mar_amount, item.apr_amount,
                        item.may_amount, item.jun_amount, item.jul_amount, item.aug_amount,
                        item.sep_amount, item.oct_amount, item.nov_amount, item.dec_amount,
                        item.notes
                    )

            return BudgetResponse(**dict(row))


@router.patch("/{budget_id}", response_model=BudgetResponse)
async def update_budget(request: Request, budget_id: UUID, data: BudgetUpdate):
    """Update budget (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only update draft budgets")

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return BudgetResponse(**dict(existing))

        set_clauses = []
        params = []
        for i, (key, value) in enumerate(update_data.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        set_clauses.append("updated_at = NOW()")
        params.extend([budget_id, ctx["tenant_id"]])

        row = await conn.fetchrow(
            f"""
            UPDATE budgets SET {', '.join(set_clauses)}
            WHERE id = ${len(params) - 1} AND tenant_id = ${len(params)}
            RETURNING *
            """,
            *params
        )

        return BudgetResponse(**dict(row))


@router.delete("/{budget_id}")
async def delete_budget(request: Request, budget_id: UUID):
    """Delete budget (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only delete draft budgets")

        await conn.execute("DELETE FROM budgets WHERE id = $1", budget_id)
        return {"message": "Budget deleted"}


# ============================================================================
# BUDGET STATUS TRANSITIONS
# ============================================================================

@router.post("/{budget_id}/approve", response_model=BudgetResponse)
async def approve_budget(request: Request, budget_id: UUID):
    """Approve budget"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only approve draft budgets")

        row = await conn.fetchrow(
            """
            UPDATE budgets SET status = 'approved', approved_at = NOW(), approved_by = $3, updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            RETURNING *
            """,
            budget_id, ctx["tenant_id"], ctx.get("user_id")
        )

        return BudgetResponse(**dict(row))


@router.post("/{budget_id}/activate", response_model=BudgetResponse)
async def activate_budget(request: Request, budget_id: UUID):
    """Activate budget for use"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        if existing["status"] not in ("draft", "approved"):
            raise HTTPException(status_code=400, detail="Can only activate draft or approved budgets")

        row = await conn.fetchrow(
            """
            UPDATE budgets SET status = 'active', updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            RETURNING *
            """,
            budget_id, ctx["tenant_id"]
        )

        return BudgetResponse(**dict(row))


@router.post("/{budget_id}/close", response_model=BudgetResponse)
async def close_budget(request: Request, budget_id: UUID):
    """Close budget"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        row = await conn.fetchrow(
            """
            UPDATE budgets SET status = 'closed', updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            RETURNING *
            """,
            budget_id, ctx["tenant_id"]
        )

        return BudgetResponse(**dict(row))


@router.post("/{budget_id}/duplicate", response_model=BudgetResponse)
async def duplicate_budget(request: Request, budget_id: UUID, new_fiscal_year: int = Query(...)):
    """Duplicate budget to new year"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        new_name = f"{existing['name']} ({new_fiscal_year})"

        async with conn.transaction():
            new_budget = await conn.fetchrow(
                """
                INSERT INTO budgets (tenant_id, name, description, fiscal_year, budget_type, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                ctx["tenant_id"], new_name, existing["description"],
                new_fiscal_year, existing["budget_type"], ctx.get("user_id")
            )

            # Copy items
            await conn.execute(
                """
                INSERT INTO budget_items (
                    budget_id, account_id, cost_center_id,
                    jan_amount, feb_amount, mar_amount, apr_amount,
                    may_amount, jun_amount, jul_amount, aug_amount,
                    sep_amount, oct_amount, nov_amount, dec_amount, notes
                )
                SELECT $2, account_id, cost_center_id,
                    jan_amount, feb_amount, mar_amount, apr_amount,
                    may_amount, jun_amount, jul_amount, aug_amount,
                    sep_amount, oct_amount, nov_amount, dec_amount, notes
                FROM budget_items WHERE budget_id = $1
                """,
                budget_id, new_budget["id"]
            )

            return BudgetResponse(**dict(new_budget))


# ============================================================================
# BUDGET ITEMS
# ============================================================================

@router.post("/{budget_id}/items", response_model=List[BudgetItemResponse])
async def upsert_budget_items(request: Request, budget_id: UUID, items: List[BudgetItemCreate]):
    """Add or update budget items (batch)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        budget = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")

        if budget["status"] not in ("draft", "approved"):
            raise HTTPException(status_code=400, detail="Cannot modify items on active/closed budget")

        async with conn.transaction():
            for item in items:
                await conn.execute(
                    """
                    INSERT INTO budget_items (
                        budget_id, account_id, cost_center_id,
                        jan_amount, feb_amount, mar_amount, apr_amount,
                        may_amount, jun_amount, jul_amount, aug_amount,
                        sep_amount, oct_amount, nov_amount, dec_amount, notes
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    ON CONFLICT (budget_id, account_id, cost_center_id)
                    DO UPDATE SET
                        jan_amount = EXCLUDED.jan_amount, feb_amount = EXCLUDED.feb_amount,
                        mar_amount = EXCLUDED.mar_amount, apr_amount = EXCLUDED.apr_amount,
                        may_amount = EXCLUDED.may_amount, jun_amount = EXCLUDED.jun_amount,
                        jul_amount = EXCLUDED.jul_amount, aug_amount = EXCLUDED.aug_amount,
                        sep_amount = EXCLUDED.sep_amount, oct_amount = EXCLUDED.oct_amount,
                        nov_amount = EXCLUDED.nov_amount, dec_amount = EXCLUDED.dec_amount,
                        notes = EXCLUDED.notes
                    """,
                    budget_id, item.account_id, item.cost_center_id,
                    item.jan_amount, item.feb_amount, item.mar_amount, item.apr_amount,
                    item.may_amount, item.jun_amount, item.jul_amount, item.aug_amount,
                    item.sep_amount, item.oct_amount, item.nov_amount, item.dec_amount,
                    item.notes
                )

        # Return updated items
        rows = await conn.fetch(
            """
            SELECT bi.*, coa.account_code, coa.name as account_name,
                   cc.code as cost_center_code, cc.name as cost_center_name
            FROM budget_items bi
            JOIN chart_of_accounts coa ON bi.account_id = coa.id
            LEFT JOIN cost_centers cc ON bi.cost_center_id = cc.id
            WHERE bi.budget_id = $1
            ORDER BY coa.account_code
            """,
            budget_id
        )

        return [BudgetItemResponse(**dict(row)) for row in rows]


@router.delete("/{budget_id}/items/{item_id}")
async def delete_budget_item(request: Request, budget_id: UUID, item_id: UUID):
    """Delete budget item"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        budget = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")

        if budget["status"] not in ("draft", "approved"):
            raise HTTPException(status_code=400, detail="Cannot modify items on active/closed budget")

        result = await conn.execute(
            "DELETE FROM budget_items WHERE id = $1 AND budget_id = $2",
            item_id, budget_id
        )

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Budget item not found")

        return {"message": "Budget item deleted"}


# ============================================================================
# BUDGET VS ACTUAL
# ============================================================================

@router.get("/{budget_id}/vs-actual", response_model=BudgetVsActualResponse)
async def get_budget_vs_actual(
    request: Request,
    budget_id: UUID,
    month: Optional[int] = Query(None, ge=1, le=12),
):
    """Get budget vs actual comparison"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        budget = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")

        rows = await conn.fetch(
            "SELECT * FROM get_budget_vs_actual($1, $2)",
            budget_id, month
        )

        items = [BudgetVsActualItem(
            account_id=row["account_id"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            account_type=row["account_type"],
            cost_center_id=row["cost_center_id"],
            cost_center_name=row["cost_center_name"],
            budget_amount=row["budget_amount"],
            actual_amount=row["actual_amount"],
            variance=row["variance"],
            percentage_used=float(row["percentage_used"]) if row["percentage_used"] else 0,
        ) for row in rows]

        total_budget = sum(i.budget_amount for i in items)
        total_actual = sum(i.actual_amount for i in items)

        return BudgetVsActualResponse(
            budget=BudgetResponse(**dict(budget)),
            month=month,
            items=items,
            total_budget=total_budget,
            total_actual=total_actual,
            total_variance=total_budget - total_actual,
        )


@router.get("/variance-alerts", response_model=VarianceAlertsResponse)
async def get_variance_alerts(
    request: Request,
    threshold_percent: float = Query(100, ge=0),
):
    """Get accounts over budget"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_variance_alerts($1, $2)",
            ctx["tenant_id"], threshold_percent
        )

        items = [VarianceAlertItem(
            budget_id=row["budget_id"],
            budget_name=row["budget_name"],
            fiscal_year=row["fiscal_year"],
            account_id=row["account_id"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            budget_amount=row["budget_amount"],
            actual_amount=row["actual_amount"],
            variance=row["variance"],
            percentage_used=float(row["percentage_used"]) if row["percentage_used"] else 0,
        ) for row in rows]

        return VarianceAlertsResponse(
            threshold_percent=threshold_percent,
            items=items,
        )


@router.get("/{budget_id}/summary", response_model=BudgetSummaryResponse)
async def get_budget_summary(request: Request, budget_id: UUID):
    """Get budget summary by account type"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        budget = await conn.fetchrow(
            "SELECT * FROM budgets WHERE id = $1 AND tenant_id = $2",
            budget_id, ctx["tenant_id"]
        )
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")

        rows = await conn.fetch("SELECT * FROM get_budget_summary($1)", budget_id)

        by_type = [BudgetSummaryByType(
            account_type=row["account_type"],
            total_budget=row["total_budget"],
            total_actual=row["total_actual"],
            total_variance=row["total_variance"],
            avg_percentage_used=float(row["avg_percentage_used"]) if row["avg_percentage_used"] else 0,
        ) for row in rows]

        return BudgetSummaryResponse(
            budget=BudgetResponse(**dict(budget)),
            by_type=by_type,
        )
