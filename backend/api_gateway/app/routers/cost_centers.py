"""
Cost Centers Router
===================
Cost center management for expense tracking by department/division/project.
NO journal entries - cost centers are dimensions/tags for analysis.
"""
from datetime import date
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.cost_centers import (
    CostCenterCreate,
    CostCenterUpdate,
    CostCenterResponse,
    CostCenterListResponse,
    CostCenterTreeNode,
    CostCenterSummaryItem,
    CostCenterSummaryResponse,
    CostCenterComparisonItem,
    CostCenterComparisonResponse,
    CostCenterTransactionItem,
    CostCenterTransactionsResponse,
)

router = APIRouter()

# Connection pool
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
# COST CENTER CRUD
# ============================================================================

@router.get("", response_model=CostCenterListResponse)
async def list_cost_centers(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    parent_id: Optional[UUID] = None,
):
    """List cost centers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if search:
            where_clauses.append(f"(code ILIKE ${param_idx} OR name ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1

        if is_active is not None:
            where_clauses.append(f"is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        if parent_id:
            where_clauses.append(f"parent_id = ${param_idx}")
            params.append(parent_id)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(f"SELECT COUNT(*) FROM cost_centers WHERE {where_sql}", *params)

        rows = await conn.fetch(
            f"""
            SELECT * FROM cost_centers
            WHERE {where_sql}
            ORDER BY path, code
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params, skip, limit
        )

        items = [CostCenterResponse(**dict(row)) for row in rows]
        return CostCenterListResponse(items=items, total=total)


@router.get("/tree")
async def get_cost_center_tree(request: Request):
    """Get cost centers as hierarchical tree"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_cost_center_tree($1)",
            ctx["tenant_id"]
        )

        # Build tree structure
        nodes = {}
        for row in rows:
            node = CostCenterTreeNode(
                id=row["id"],
                code=row["code"],
                name=row["name"],
                description=row["description"],
                parent_id=row["parent_id"],
                level=row["level"],
                path=row["path"],
                manager_name=row["manager_name"],
                is_active=row["is_active"],
                tenant_id=ctx["tenant_id"],
                created_at=None,
                updated_at=None,
                children_count=row["children_count"],
                children=[]
            )
            nodes[row["id"]] = node

        # Build tree
        root_nodes = []
        for node in nodes.values():
            if node.parent_id and node.parent_id in nodes:
                nodes[node.parent_id].children.append(node)
            else:
                root_nodes.append(node)

        return root_nodes


@router.get("/{cost_center_id}", response_model=CostCenterResponse)
async def get_cost_center(request: Request, cost_center_id: UUID):
    """Get single cost center"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            "SELECT * FROM cost_centers WHERE id = $1 AND tenant_id = $2",
            cost_center_id, ctx["tenant_id"]
        )
        if not row:
            raise HTTPException(status_code=404, detail="Cost center not found")

        return CostCenterResponse(**dict(row))


@router.post("", response_model=CostCenterResponse, status_code=201)
async def create_cost_center(request: Request, data: CostCenterCreate):
    """Create cost center"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Check code uniqueness
        exists = await conn.fetchval(
            "SELECT 1 FROM cost_centers WHERE tenant_id = $1 AND code = $2",
            ctx["tenant_id"], data.code
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"Cost center code '{data.code}' already exists")

        # Validate parent
        if data.parent_id:
            parent = await conn.fetchrow(
                "SELECT id FROM cost_centers WHERE id = $1 AND tenant_id = $2",
                data.parent_id, ctx["tenant_id"]
            )
            if not parent:
                raise HTTPException(status_code=400, detail="Parent cost center not found")

        row = await conn.fetchrow(
            """
            INSERT INTO cost_centers (tenant_id, code, name, description, parent_id, manager_name, manager_email)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            ctx["tenant_id"], data.code, data.name, data.description,
            data.parent_id, data.manager_name, data.manager_email
        )

        return CostCenterResponse(**dict(row))


@router.patch("/{cost_center_id}", response_model=CostCenterResponse)
async def update_cost_center(request: Request, cost_center_id: UUID, data: CostCenterUpdate):
    """Update cost center"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM cost_centers WHERE id = $1 AND tenant_id = $2",
            cost_center_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Cost center not found")

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return CostCenterResponse(**dict(existing))

        # Check code uniqueness if changing
        if "code" in update_data and update_data["code"] != existing["code"]:
            exists = await conn.fetchval(
                "SELECT 1 FROM cost_centers WHERE tenant_id = $1 AND code = $2 AND id != $3",
                ctx["tenant_id"], update_data["code"], cost_center_id
            )
            if exists:
                raise HTTPException(status_code=400, detail=f"Cost center code already exists")

        # Validate parent if changing
        if "parent_id" in update_data and update_data["parent_id"]:
            if update_data["parent_id"] == cost_center_id:
                raise HTTPException(status_code=400, detail="Cost center cannot be its own parent")
            parent = await conn.fetchrow(
                "SELECT id FROM cost_centers WHERE id = $1 AND tenant_id = $2",
                update_data["parent_id"], ctx["tenant_id"]
            )
            if not parent:
                raise HTTPException(status_code=400, detail="Parent cost center not found")

        set_clauses = []
        params = []
        for i, (key, value) in enumerate(update_data.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        set_clauses.append(f"updated_at = NOW()")
        params.extend([cost_center_id, ctx["tenant_id"]])

        row = await conn.fetchrow(
            f"""
            UPDATE cost_centers SET {', '.join(set_clauses)}
            WHERE id = ${len(params) - 1} AND tenant_id = ${len(params)}
            RETURNING *
            """,
            *params
        )

        return CostCenterResponse(**dict(row))


@router.delete("/{cost_center_id}")
async def delete_cost_center(request: Request, cost_center_id: UUID):
    """Deactivate cost center (soft delete)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM cost_centers WHERE id = $1 AND tenant_id = $2",
            cost_center_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Cost center not found")

        # Check for children
        children = await conn.fetchval(
            "SELECT COUNT(*) FROM cost_centers WHERE parent_id = $1",
            cost_center_id
        )
        if children > 0:
            raise HTTPException(status_code=400, detail="Cannot delete cost center with children")

        # Check for usage in journal_lines
        usage = await conn.fetchval(
            "SELECT COUNT(*) FROM journal_lines WHERE cost_center_id = $1",
            cost_center_id
        )
        if usage > 0:
            # Soft delete
            await conn.execute(
                "UPDATE cost_centers SET is_active = false, updated_at = NOW() WHERE id = $1",
                cost_center_id
            )
            return {"message": "Cost center deactivated (has transactions)"}

        # Hard delete if no usage
        await conn.execute("DELETE FROM cost_centers WHERE id = $1", cost_center_id)
        return {"message": "Cost center deleted"}


# ============================================================================
# REPORTS
# ============================================================================

@router.get("/{cost_center_id}/summary", response_model=CostCenterSummaryResponse)
async def get_cost_center_summary(
    request: Request,
    cost_center_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Get cost center summary by account"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        cc = await conn.fetchrow(
            "SELECT * FROM cost_centers WHERE id = $1 AND tenant_id = $2",
            cost_center_id, ctx["tenant_id"]
        )
        if not cc:
            raise HTTPException(status_code=404, detail="Cost center not found")

        rows = await conn.fetch(
            "SELECT * FROM get_cost_center_summary($1, $2, $3)",
            cost_center_id, start_date, end_date
        )

        items = [CostCenterSummaryItem(
            account_type=row["account_type"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            total_debit=row["total_debit"],
            total_credit=row["total_credit"],
            net_amount=row["net_amount"],
        ) for row in rows]

        total_debit = sum(i.total_debit for i in items)
        total_credit = sum(i.total_credit for i in items)

        return CostCenterSummaryResponse(
            cost_center=CostCenterResponse(**dict(cc)),
            start_date=start_date,
            end_date=end_date,
            items=items,
            total_debit=total_debit,
            total_credit=total_credit,
            total_net=total_debit - total_credit,
        )


@router.get("/{cost_center_id}/transactions", response_model=CostCenterTransactionsResponse)
async def get_cost_center_transactions(
    request: Request,
    cost_center_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Get transactions for cost center"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        cc = await conn.fetchrow(
            "SELECT * FROM cost_centers WHERE id = $1 AND tenant_id = $2",
            cost_center_id, ctx["tenant_id"]
        )
        if not cc:
            raise HTTPException(status_code=404, detail="Cost center not found")

        rows = await conn.fetch(
            """
            SELECT
                je.id as journal_id,
                je.entry_date,
                je.reference,
                je.description,
                coa.account_code,
                coa.name as account_name,
                jl.debit,
                jl.credit
            FROM journal_lines jl
            JOIN journal_entries je ON jl.journal_id = je.id
            JOIN chart_of_accounts coa ON jl.account_id = coa.id
            WHERE jl.cost_center_id = $1
            AND je.status = 'POSTED'
            AND je.entry_date BETWEEN $2 AND $3
            ORDER BY je.entry_date DESC, je.id
            OFFSET $4 LIMIT $5
            """,
            cost_center_id, start_date, end_date, skip, limit
        )

        transactions = [CostCenterTransactionItem(
            journal_id=row["journal_id"],
            entry_date=row["entry_date"],
            reference=row["reference"],
            description=row["description"],
            account_code=row["account_code"],
            account_name=row["account_name"],
            debit=row["debit"],
            credit=row["credit"],
        ) for row in rows]

        total_debit = sum(t.debit for t in transactions)
        total_credit = sum(t.credit for t in transactions)

        return CostCenterTransactionsResponse(
            cost_center=CostCenterResponse(**dict(cc)),
            start_date=start_date,
            end_date=end_date,
            transactions=transactions,
            total_debit=total_debit,
            total_credit=total_credit,
        )


@router.get("/comparison", response_model=CostCenterComparisonResponse)
async def compare_cost_centers(
    request: Request,
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Compare all cost centers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM compare_cost_centers($1, $2, $3)",
            ctx["tenant_id"], start_date, end_date
        )

        items = [CostCenterComparisonItem(
            cost_center_id=row["cost_center_id"],
            cost_center_code=row["cost_center_code"],
            cost_center_name=row["cost_center_name"],
            total_revenue=row["total_revenue"],
            total_expense=row["total_expense"],
            net_amount=row["net_amount"],
        ) for row in rows]

        return CostCenterComparisonResponse(
            start_date=start_date,
            end_date=end_date,
            items=items,
        )
