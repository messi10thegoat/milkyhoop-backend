"""
Router for Production Costing (Kalkulasi Harga Produksi)
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.production_costing import (
    CostingResponse,
    CostPoolListResponse,
    CreateCostPoolRequest,
    CreateStandardCostRequest,
    StandardCostListResponse,
    VarianceSummaryResponse,
)

router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
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
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


# =============================================================================
# HEALTH CHECK
# =============================================================================

@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "production_costing"}


# =============================================================================
# STANDARD COSTS
# =============================================================================

@router.get("/standard-costs", response_model=StandardCostListResponse)
async def list_standard_costs(
    request: Request,
    product_id: Optional[UUID] = None,
    effective_date: Optional[date] = None,
    source: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List standard costs with optional filters."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["sc.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if product_id:
            where_clauses.append(f"sc.product_id = ${param_idx}")
            params.append(product_id)
            param_idx += 1

        if effective_date:
            where_clauses.append(f"sc.effective_date <= ${param_idx}")
            params.append(effective_date)
            param_idx += 1
            where_clauses.append(f"(sc.end_date IS NULL OR sc.end_date >= ${param_idx})")
            params.append(effective_date)
            param_idx += 1

        if source:
            where_clauses.append(f"sc.source = ${param_idx}")
            params.append(source)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        # Count
        count_sql = f"""
            SELECT COUNT(*) FROM standard_costs sc
            WHERE {where_sql}
        """
        total = await conn.fetchval(count_sql, *params)

        # List
        params.extend([limit, offset])
        list_sql = f"""
            SELECT
                sc.id,
                sc.product_id,
                p.nama_produk as product_name,
                sc.effective_date,
                sc.end_date,
                sc.material_cost,
                sc.labor_cost,
                sc.overhead_cost,
                (sc.material_cost + sc.labor_cost + sc.overhead_cost) as total_cost,
                sc.source
            FROM standard_costs sc
            JOIN products p ON p.id = sc.product_id
            WHERE {where_sql}
            ORDER BY sc.effective_date DESC, p.nama_produk
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """

        rows = await conn.fetch(list_sql, *params)

        items = [{
            "id": str(r["id"]),
            "product_id": str(r["product_id"]),
            "product_name": r["product_name"],
            "effective_date": r["effective_date"],
            "end_date": r["end_date"],
            "material_cost": r["material_cost"],
            "labor_cost": r["labor_cost"],
            "overhead_cost": r["overhead_cost"],
            "total_cost": r["total_cost"],
            "source": r["source"]
        } for r in rows]

        return StandardCostListResponse(
            items=items,
            total=total,
            has_more=(offset + len(items)) < total
        )


@router.post("/standard-costs", response_model=CostingResponse)
async def create_standard_cost(
    request: Request,
    data: CreateStandardCostRequest
):
    """Create a new standard cost record."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Verify product exists
            product = await conn.fetchrow(
                "SELECT id, name FROM products WHERE id = $1 AND tenant_id = $2",
                data.product_id, ctx["tenant_id"]
            )
            if not product:
                raise HTTPException(status_code=404, detail="Product not found")

            # End any existing active standard cost
            await conn.execute("""
                UPDATE standard_costs
                SET end_date = $1
                WHERE product_id = $2 AND tenant_id = $3
                AND end_date IS NULL AND effective_date < $1
            """, data.effective_date, data.product_id, ctx["tenant_id"])

            # Insert new standard cost
            row = await conn.fetchrow("""
                INSERT INTO standard_costs (
                    tenant_id, product_id, effective_date,
                    material_cost, labor_cost, overhead_cost,
                    source, bom_id, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id, material_cost + labor_cost + overhead_cost as total_cost
            """,
                ctx["tenant_id"], data.product_id, data.effective_date,
                data.material_cost, data.labor_cost, data.overhead_cost,
                data.source, data.bom_id, ctx["user_id"]
            )

            return CostingResponse(
                success=True,
                message="Standard cost created successfully",
                data={
                    "id": str(row["id"]),
                    "total_cost": row["total_cost"]
                }
            )


@router.post("/standard-costs/calculate-from-bom/{bom_id}", response_model=CostingResponse)
async def calculate_standard_cost_from_bom(
    request: Request,
    bom_id: UUID,
    effective_date: date = Query(default=None)
):
    """Calculate standard cost from BOM components and operations."""
    ctx = get_user_context(request)
    pool = await get_pool()

    if effective_date is None:
        effective_date = date.today()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        # Get BOM with product
        bom = await conn.fetchrow("""
            SELECT b.id, b.product_id, p.nama_produk as product_name, b.output_quantity
            FROM bom b
            JOIN products p ON p.id = b.product_id
            WHERE b.id = $1 AND b.tenant_id = $2
        """, bom_id, ctx["tenant_id"])

        if not bom:
            raise HTTPException(status_code=404, detail="BOM not found")

        # Calculate material cost from components
        material_cost = await conn.fetchval("""
            SELECT COALESCE(SUM(
                bc.quantity * COALESCE(
                    (SELECT sc.material_cost + sc.labor_cost + sc.overhead_cost
                     FROM standard_costs sc
                     WHERE sc.product_id = bc.component_product_id
                     AND sc.effective_date <= $2
                     AND (sc.end_date IS NULL OR sc.end_date >= $2)
                     ORDER BY sc.effective_date DESC LIMIT 1),
                    p.unit_cost
                )
            ), 0)::BIGINT
            FROM bom_components bc
            JOIN products p ON p.id = bc.component_product_id
            WHERE bc.bom_id = $1
        """, bom_id, effective_date)

        # Calculate labor cost from operations
        labor_cost = await conn.fetchval("""
            SELECT COALESCE(SUM(
                bo.setup_time_minutes * COALESCE(wc.labor_rate_per_hour, 0) / 60 +
                bo.run_time_minutes * COALESCE(wc.labor_rate_per_hour, 0) / 60
            ), 0)::BIGINT
            FROM bom_operations bo
            LEFT JOIN work_centers wc ON wc.id = bo.work_center_id
            WHERE bo.bom_id = $1
        """, bom_id)

        # Calculate overhead from operations
        overhead_cost = await conn.fetchval("""
            SELECT COALESCE(SUM(
                bo.run_time_minutes * COALESCE(wc.overhead_rate_per_hour, 0) / 60
            ), 0)::BIGINT
            FROM bom_operations bo
            LEFT JOIN work_centers wc ON wc.id = bo.work_center_id
            WHERE bo.bom_id = $1
        """, bom_id)

        # Divide by output quantity
        output_qty = float(bom["output_quantity"]) if bom["output_quantity"] else 1.0
        material_cost = int(material_cost / output_qty)
        labor_cost = int(labor_cost / output_qty)
        overhead_cost = int(overhead_cost / output_qty)

        return CostingResponse(
            success=True,
            message="Standard cost calculated from BOM",
            data={
                "product_id": str(bom["product_id"]),
                "product_name": bom["product_name"],
                "bom_id": str(bom_id),
                "effective_date": str(effective_date),
                "material_cost": material_cost,
                "labor_cost": labor_cost,
                "overhead_cost": overhead_cost,
                "total_cost": material_cost + labor_cost + overhead_cost
            }
        )


# =============================================================================
# COST POOLS
# =============================================================================

@router.get("/cost-pools", response_model=CostPoolListResponse)
async def list_cost_pools(
    request: Request,
    fiscal_year: Optional[int] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List cost pools."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["cp.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if fiscal_year:
            where_clauses.append(f"cp.fiscal_year = ${param_idx}")
            params.append(fiscal_year)
            param_idx += 1

        if is_active is not None:
            where_clauses.append(f"cp.is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        count_sql = f"SELECT COUNT(*) FROM cost_pools cp WHERE {where_sql}"
        total = await conn.fetchval(count_sql, *params)

        params.extend([limit, offset])
        list_sql = f"""
            SELECT
                cp.id, cp.code, cp.nama_produk, cp.pool_type,
                cp.allocation_basis, cp.budgeted_amount,
                cp.actual_amount, cp.is_active,
                CASE WHEN cp.budgeted_basis_quantity > 0
                     THEN (cp.budgeted_amount / cp.budgeted_basis_quantity)::BIGINT
                     ELSE 0 END as rate_per_unit
            FROM cost_pools cp
            WHERE {where_sql}
            ORDER BY cp.code
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """

        rows = await conn.fetch(list_sql, *params)

        items = [{
            "id": str(r["id"]),
            "code": r["code"],
            "name": r["name"],
            "pool_type": r["pool_type"],
            "allocation_basis": r["allocation_basis"],
            "budgeted_amount": r["budgeted_amount"],
            "actual_amount": r["actual_amount"],
            "rate_per_unit": r["rate_per_unit"],
            "is_active": r["is_active"]
        } for r in rows]

        return CostPoolListResponse(items=items, total=total)


@router.post("/cost-pools", response_model=CostingResponse)
async def create_cost_pool(
    request: Request,
    data: CreateCostPoolRequest
):
    """Create a new cost pool."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        # Check duplicate code
        exists = await conn.fetchval("""
            SELECT 1 FROM cost_pools
            WHERE tenant_id = $1 AND code = $2 AND fiscal_year = $3
        """, ctx["tenant_id"], data.code, data.fiscal_year)

        if exists:
            raise HTTPException(
                status_code=400,
                detail=f"Cost pool with code {data.code} already exists for fiscal year {data.fiscal_year}"
            )

        row = await conn.fetchrow("""
            INSERT INTO cost_pools (
                tenant_id, code, name, description, pool_type,
                allocation_basis, budgeted_amount, budgeted_basis_quantity,
                fiscal_year, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
        """,
            ctx["tenant_id"], data.code, data.name, data.description,
            data.pool_type, data.allocation_basis, data.budgeted_amount,
            data.budgeted_basis_quantity, data.fiscal_year, ctx["user_id"]
        )

        return CostingResponse(
            success=True,
            message="Cost pool created successfully",
            data={"id": str(row["id"])}
        )


@router.post("/cost-pools/{pool_id}/record-actual", response_model=CostingResponse)
async def record_actual_cost(
    request: Request,
    pool_id: UUID,
    amount: int = Query(..., ge=0),
    description: Optional[str] = None
):
    """Record actual cost to a cost pool."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Get cost pool
            cost_pool = await conn.fetchrow("""
                SELECT id, actual_amount FROM cost_pools
                WHERE id = $1 AND tenant_id = $2
            """, pool_id, ctx["tenant_id"])

            if not cost_pool:
                raise HTTPException(status_code=404, detail="Cost pool not found")

            # Update actual amount
            new_actual = cost_pool["actual_amount"] + amount
            await conn.execute("""
                UPDATE cost_pools
                SET actual_amount = $1, updated_at = NOW()
                WHERE id = $2
            """, new_actual, pool_id)

            return CostingResponse(
                success=True,
                message="Actual cost recorded",
                data={
                    "pool_id": str(pool_id),
                    "amount_added": amount,
                    "new_actual_amount": new_actual
                }
            )


# =============================================================================
# VARIANCE ANALYSIS
# =============================================================================

@router.get("/variance/{product_id}", response_model=VarianceSummaryResponse)
async def get_variance_analysis(
    request: Request,
    product_id: UUID,
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12)
):
    """Get variance analysis for a product in a given period."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        # Get product info
        product = await conn.fetchrow("""
            SELECT id, name FROM products
            WHERE id = $1 AND tenant_id = $2
        """, product_id, ctx["tenant_id"])

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Get produced quantity for the period
        produced_qty = await conn.fetchval("""
            SELECT COALESCE(SUM(poc.quantity_good), 0)
            FROM production_order_completions poc
            JOIN production_orders po ON po.id = poc.production_order_id
            WHERE po.product_id = $1 AND po.tenant_id = $2
            AND EXTRACT(YEAR FROM poc.completion_date) = $3
            AND EXTRACT(MONTH FROM poc.completion_date) = $4
        """, product_id, ctx["tenant_id"], year, month)

        if produced_qty == 0:
            return VarianceSummaryResponse(
                success=True,
                product_id=str(product_id),
                product_name=product["name"],
                period_year=year,
                period_month=month,
                produced_quantity=Decimal("0"),
                analysis=[],
                total_variance=0
            )

        # Get standard cost for the period
        period_date = date(year, month, 1)
        standard = await conn.fetchrow("""
            SELECT material_cost, labor_cost, overhead_cost
            FROM standard_costs
            WHERE product_id = $1 AND tenant_id = $2
            AND effective_date <= $3
            AND (end_date IS NULL OR end_date >= $3)
            ORDER BY effective_date DESC
            LIMIT 1
        """, product_id, ctx["tenant_id"], period_date)

        if not standard:
            raise HTTPException(
                status_code=400,
                detail="No standard cost defined for this product and period"
            )

        # Get actual costs from production orders
        actual_materials = await conn.fetchval("""
            SELECT COALESCE(SUM(pom.actual_quantity * pom.unit_cost), 0)::BIGINT
            FROM production_order_materials pom
            JOIN production_orders po ON po.id = pom.production_order_id
            WHERE po.product_id = $1 AND po.tenant_id = $2
            AND EXTRACT(YEAR FROM po.start_date) = $3
            AND EXTRACT(MONTH FROM po.start_date) = $4
        """, product_id, ctx["tenant_id"], year, month)

        actual_labor = await conn.fetchval("""
            SELECT COALESCE(SUM(pol.labor_cost), 0)::BIGINT
            FROM production_order_labor pol
            JOIN production_orders po ON po.id = pol.production_order_id
            WHERE po.product_id = $1 AND po.tenant_id = $2
            AND EXTRACT(YEAR FROM po.start_date) = $3
            AND EXTRACT(MONTH FROM po.start_date) = $4
        """, product_id, ctx["tenant_id"], year, month)

        # Calculate standard costs for produced quantity
        std_material = int(standard["material_cost"] * float(produced_qty))
        std_labor = int(standard["labor_cost"] * float(produced_qty))
        std_overhead = int(standard["overhead_cost"] * float(produced_qty))

        # Calculate overhead (simplified - proportional to labor)
        actual_overhead = int(actual_labor * 0.5) if actual_labor > 0 else 0

        analysis = []

        # Material variance
        mat_variance = actual_materials - std_material
        analysis.append({
            "category": "Material",
            "standard": std_material,
            "actual": actual_materials,
            "variance": mat_variance,
            "variance_type": "favorable" if mat_variance <= 0 else "unfavorable"
        })

        # Labor variance
        labor_variance = actual_labor - std_labor
        analysis.append({
            "category": "Labor",
            "standard": std_labor,
            "actual": actual_labor,
            "variance": labor_variance,
            "variance_type": "favorable" if labor_variance <= 0 else "unfavorable"
        })

        # Overhead variance
        overhead_variance = actual_overhead - std_overhead
        analysis.append({
            "category": "Overhead",
            "standard": std_overhead,
            "actual": actual_overhead,
            "variance": overhead_variance,
            "variance_type": "favorable" if overhead_variance <= 0 else "unfavorable"
        })

        total_variance = mat_variance + labor_variance + overhead_variance

        return VarianceSummaryResponse(
            success=True,
            product_id=str(product_id),
            product_name=product["name"],
            period_year=year,
            period_month=month,
            produced_quantity=Decimal(str(produced_qty)),
            analysis=analysis,
            total_variance=total_variance
        )


# =============================================================================
# OVERHEAD ALLOCATION
# =============================================================================

@router.post("/allocate-overhead", response_model=CostingResponse)
async def allocate_overhead(
    request: Request,
    fiscal_year: int = Query(...),
    period_month: int = Query(..., ge=1, le=12)
):
    """Allocate overhead from cost pools to production orders."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Get active cost pools for the fiscal year
            cost_pools = await conn.fetch("""
                SELECT id, code, name, allocation_basis, budgeted_amount,
                       actual_amount, budgeted_basis_quantity
                FROM cost_pools
                WHERE tenant_id = $1 AND fiscal_year = $2 AND is_active = true
            """, ctx["tenant_id"], fiscal_year)

            if not cost_pools:
                raise HTTPException(
                    status_code=400,
                    detail="No active cost pools found for the fiscal year"
                )

            allocations = []

            for cp in cost_pools:
                # Get allocation basis total for the period
                if cp["allocation_basis"] == "direct_labor_hours":
                    basis_total = await conn.fetchval("""
                        SELECT COALESCE(SUM(pol.hours_worked), 0)
                        FROM production_order_labor pol
                        JOIN production_orders po ON po.id = pol.production_order_id
                        WHERE po.tenant_id = $1
                        AND EXTRACT(YEAR FROM pol.work_date) = $2
                        AND EXTRACT(MONTH FROM pol.work_date) = $3
                    """, ctx["tenant_id"], fiscal_year, period_month)
                elif cp["allocation_basis"] == "units_produced":
                    basis_total = await conn.fetchval("""
                        SELECT COALESCE(SUM(poc.quantity_good), 0)
                        FROM production_order_completions poc
                        JOIN production_orders po ON po.id = poc.production_order_id
                        WHERE po.tenant_id = $1
                        AND EXTRACT(YEAR FROM poc.completion_date) = $2
                        AND EXTRACT(MONTH FROM poc.completion_date) = $3
                    """, ctx["tenant_id"], fiscal_year, period_month)
                else:
                    basis_total = Decimal("0")

                if basis_total > 0 and cp["budgeted_basis_quantity"] > 0:
                    rate = cp["budgeted_amount"] / float(cp["budgeted_basis_quantity"])
                    allocated_amount = int(float(basis_total) * rate)

                    # Record allocation
                    await conn.execute("""
                        INSERT INTO overhead_allocations (
                            tenant_id, cost_pool_id, allocation_date,
                            allocated_amount, basis_quantity, rate_applied, created_by
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        ctx["tenant_id"], cp["id"], date(fiscal_year, period_month, 1),
                        allocated_amount, basis_total, Decimal(str(rate)), ctx["user_id"]
                    )

                    allocations.append({
                        "cost_pool_code": cp["code"],
                        "cost_pool_name": cp["name"],
                        "basis_total": float(basis_total),
                        "rate": rate,
                        "allocated_amount": allocated_amount
                    })

            return CostingResponse(
                success=True,
                message=f"Overhead allocated for {period_month}/{fiscal_year}",
                data={
                    "allocations": allocations,
                    "total_allocated": sum(a["allocated_amount"] for a in allocations)
                }
            )
