"""
Production Router - Production Order Management

Manages production orders, material issuance, labor tracking, and completions.

Journal Entries:
- Issue Materials: Dr. WIP / Cr. Inventory
- Record Labor: Dr. WIP / Cr. Direct Labor
- Apply Overhead: Dr. WIP / Cr. Manufacturing Overhead
- Complete Production: Dr. Finished Goods / Cr. WIP
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal, List
from uuid import UUID
from datetime import date
from decimal import Decimal
import logging
import asyncpg

from ..schemas.production import (
    CreateProductionOrderRequest,
    UpdateProductionOrderRequest,
    ProductionOrderListResponse,
    ProductionOrderDetailResponse,
    ProductionMaterialInput,
    ProductionLaborInput,
    ProductionCompletionInput,
    CostAnalysisResponse,
    ProductionScheduleResponse,
    ProductionResponse,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)
from ..services.role_precondition import assert_required_roles_for_path

logger = logging.getLogger(__name__)
router = APIRouter()

# Fase D3.3: role-based CoA resolution for manufaktur posting paths.
# Replaces hardcoded literals 1-10650 / 1-10600 / 5-90200 (cancel order
# variance, material issue, FG receipt). Void/reverse paths read
# account_id from journal_lines (Law 2/26) and are NOT touched.
_PRODUCTION_REQUIRED_ROLES = [
    AccountRole.WIP_GENERIC,
    AccountRole.COGS_VARIANCE_PRODUCTION,
    AccountRole.INVENTORY_MERCHANDISE,
]
_production_precondition_checked_tenants: set = set()


async def _ensure_production_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition once per tenant for production.

    Fails loud (PreconditionFailedError) if any tenant lacks any required
    role mapping. After first successful check the audit is skipped.
    """
    if tenant_id is None:
        # legacy/global fallback (unchanged behavior)
        await assert_required_roles_for_path(
            pool, "production", _PRODUCTION_REQUIRED_ROLES
        )
        return
    if tenant_id in _production_precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "production", _PRODUCTION_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _production_precondition_checked_tenants.add(tenant_id)


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


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
# HEALTH CHECK
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "production"}


# =============================================================================
# AGGREGATE VIEWS — Material Issues & FG Receipts across all WOs
# Must be defined BEFORE /{order_id} routes
# =============================================================================


@router.get("/material-issues")
async def list_material_issues_aggregate(
    request: Request,
    limit: int = Query(500, ge=1, le=2000),
):
    """Aggregate list of issued materials across all work orders (for Pengeluaran Bahan page)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    pom.id,
                    po.id AS production_order_id,
                    po.order_number AS wo_number,
                    po.status AS order_status,
                    fg.nama_produk AS fg_product_name,
                    pom.product_id,
                    p.nama_produk AS product_name,
                    p.sku AS product_sku,
                    pom.planned_quantity,
                    pom.issued_quantity,
                    pom.unit,
                    pom.planned_cost,
                    pom.actual_cost,
                    pom.issued_date,
                    pom.warehouse_id
                FROM production_order_materials pom
                JOIN production_orders po ON po.id = pom.production_order_id
                JOIN products p ON p.id = pom.product_id
                LEFT JOIN products fg ON fg.id = po.product_id
                WHERE po.tenant_id = $1
                ORDER BY pom.issued_date DESC NULLS LAST, po.order_number DESC
                LIMIT $2
                """,
                ctx["tenant_id"],
                limit,
            )
            items = [
                {
                    "id": str(r["id"]),
                    "production_order_id": str(r["production_order_id"]),
                    "wo_number": r["wo_number"],
                    "order_status": r["order_status"],
                    "fg_product_name": r["fg_product_name"],
                    "product_id": str(r["product_id"]),
                    "product_name": r["product_name"],
                    "product_sku": r["product_sku"],
                    "planned_quantity": float(r["planned_quantity"] or 0),
                    "issued_quantity": float(r["issued_quantity"] or 0),
                    "unit": r["unit"],
                    "planned_cost": float(r["planned_cost"] or 0),
                    "actual_cost": float(r["actual_cost"] or 0),
                    "issued_date": r["issued_date"].isoformat()
                    if r["issued_date"]
                    else None,
                }
                for r in rows
            ]
            return {"items": items, "total": len(items)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing material issues: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list material issues")


@router.get("/fg-receipts")
async def list_fg_receipts_aggregate(
    request: Request,
    limit: int = Query(500, ge=1, le=2000),
):
    """Aggregate list of FG completions across all work orders (for Penerimaan Produksi page)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    pc.id,
                    po.id AS production_order_id,
                    po.order_number AS wo_number,
                    po.status AS order_status,
                    p.nama_produk AS product_name,
                    p.sku AS product_sku,
                    po.unit AS unit,
                    pc.good_quantity,
                    pc.scrap_quantity,
                    pc.quality_status,
                    pc.unit_cost,
                    pc.total_cost,
                    pc.completion_date,
                    pc.warehouse_id,
                    pc.inspection_notes
                FROM production_completions pc
                JOIN production_orders po ON po.id = pc.production_order_id
                JOIN products p ON p.id = po.product_id
                WHERE po.tenant_id = $1
                ORDER BY pc.completion_date DESC NULLS LAST, pc.created_at DESC
                LIMIT $2
                """,
                ctx["tenant_id"],
                limit,
            )
            items = [
                {
                    "id": str(r["id"]),
                    "production_order_id": str(r["production_order_id"]),
                    "wo_number": r["wo_number"],
                    "order_status": r["order_status"],
                    "product_name": r["product_name"],
                    "product_sku": r["product_sku"],
                    "unit": r["unit"] or "pcs",
                    "good_quantity": float(r["good_quantity"] or 0),
                    "scrap_quantity": float(r["scrap_quantity"] or 0),
                    "quality_status": r["quality_status"] or "passed",
                    "unit_cost": float(r["unit_cost"] or 0),
                    "total_cost": float(r["total_cost"] or 0),
                    "completion_date": r["completion_date"].isoformat()
                    if r["completion_date"]
                    else None,
                    "inspection_notes": r["inspection_notes"],
                }
                for r in rows
            ]
            return {"items": items, "total": len(items)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing FG receipts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list FG receipts")


# =============================================================================
# PRODUCTION ORDERS
# =============================================================================


# Alias endpoint for /api/production/orders (must be defined BEFORE /{order_id})
@router.get("/orders", response_model=ProductionOrderListResponse)
async def list_production_orders_alias(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    product_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[int] = Query(None, ge=1, le=10),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    sort_by: Literal["order_number", "order_date", "priority"] = Query("order_date"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List production orders (alias for /api/production/orders)."""
    # Forward to main list endpoint
    return await list_production_orders(
        request=request,
        skip=skip,
        limit=limit,
        search=search,
        product_id=product_id,
        status=status,
        priority=priority,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("", response_model=ProductionOrderListResponse)
async def list_production_orders(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    product_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[int] = Query(None, ge=1, le=10),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    sort_by: Literal["order_number", "order_date", "priority"] = Query("order_date"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List production orders."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["po.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(
                    f"(po.order_number ILIKE ${param_idx} OR p.nama_produk ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if product_id:
                conditions.append(f"po.product_id = ${param_idx}")
                params.append(product_id)
                param_idx += 1

            if status:
                conditions.append(f"po.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if priority:
                conditions.append(f"po.priority = ${param_idx}")
                params.append(priority)
                param_idx += 1

            if start_date:
                conditions.append(f"po.planned_start_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"po.planned_end_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            sort_column = {
                "order_number": "po.order_number",
                "order_date": "po.order_date",
                "priority": "po.priority",
            }[sort_by]

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM production_orders po JOIN products p ON p.id = po.product_id WHERE {where_clause}",
                *params,
            )

            query = f"""
                SELECT po.*, p.nama_produk as product_name, p.sku as product_sku,
                       COALESCE((
                           SELECT BOOL_AND(COALESCE(pom.issued_quantity,0) >= COALESCE(pom.planned_quantity, 0))
                           FROM production_order_materials pom
                           WHERE pom.production_order_id = po.id
                       ), FALSE) AS all_materials_issued
                FROM production_orders po
                JOIN products p ON p.id = po.product_id
                WHERE {where_clause}
                ORDER BY {sort_column} {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = []
            for row in rows:
                completion_pct = 0
                if row["planned_quantity"] > 0:
                    completion_pct = round(
                        Decimal(str(row["completed_quantity"]))
                        / Decimal(str(row["planned_quantity"]))
                        * 100,
                        2,
                    )

                items.append(
                    {
                        "id": str(row["id"]),
                        "order_number": row["order_number"],
                        "order_date": row["order_date"],
                        "product_id": str(row["product_id"]),
                        "product_name": row["product_name"],
                        "product_sku": row["product_sku"],
                        "planned_quantity": row["planned_quantity"],
                        "completed_quantity": row["completed_quantity"],
                        "status": row["status"],
                        "priority": row["priority"],
                        "planned_start_date": row["planned_start_date"],
                        "planned_end_date": row["planned_end_date"],
                        "completion_percent": Decimal(str(completion_pct)),
                        "created_at": row["created_at"],
                        "all_materials_issued": bool(
                            row.get("all_materials_issued") or False
                        ),
                    }
                )

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing production orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list production orders")


@router.post("", response_model=ProductionResponse, status_code=201)
async def create_production_order(request: Request, body: CreateProductionOrderRequest):
    """Create production order from BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get BOM info
                bom = await conn.fetchrow(
                    """
                    SELECT bom.*, p.nama_produk as product_name
                    FROM bill_of_materials bom
                    JOIN products p ON p.id = bom.product_id
                    WHERE bom.tenant_id = $1 AND bom.id = $2 AND bom.status = 'active'
                    """,
                    ctx["tenant_id"],
                    body.bom_id,
                )
                if not bom:
                    raise HTTPException(status_code=400, detail="Active BOM not found")

                # Generate order number
                order_number = await conn.fetchval(
                    "SELECT generate_production_order_number($1)", ctx["tenant_id"]
                )

                # Calculate planned costs based on BOM
                multiplier = Decimal(str(body.planned_quantity)) / Decimal(
                    str(bom["output_quantity"])
                )
                planned_material = int(Decimal(str(bom["standard_cost"])) * multiplier)
                planned_labor = int(Decimal(str(bom["labor_cost"])) * multiplier)
                planned_overhead = int(Decimal(str(bom["overhead_cost"])) * multiplier)

                # Create production order
                order_id = await conn.fetchval(
                    """
                    INSERT INTO production_orders (
                        tenant_id, order_number, order_date, product_id, bom_id,
                        planned_quantity, unit, planned_start_date, planned_end_date,
                        work_center_id, warehouse_id, sales_order_id, customer_id,
                        planned_material_cost, planned_labor_cost, planned_overhead_cost,
                        priority, notes, created_by
                    ) VALUES ($1, $2, CURRENT_DATE, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                    RETURNING id
                    """,
                    ctx["tenant_id"],
                    order_number,
                    body.product_id,
                    body.bom_id,
                    body.planned_quantity,
                    body.unit,
                    body.planned_start_date,
                    body.planned_end_date,
                    body.work_center_id,
                    body.warehouse_id,
                    body.sales_order_id,
                    body.customer_id,
                    planned_material,
                    planned_labor,
                    planned_overhead,
                    body.priority,
                    body.notes,
                    ctx["user_id"],
                )

                # Create planned materials from BOM components
                components = await conn.fetch(
                    """
                    SELECT bc.*, p.nama_produk as product_name
                    FROM bom_components bc
                    JOIN products p ON p.id = bc.component_product_id
                    WHERE bc.bom_id = $1
                    """,
                    body.bom_id,
                )

                for comp in components:
                    planned_qty = (
                        Decimal(str(comp["quantity"]))
                        * multiplier
                        * (1 + Decimal(str(comp["wastage_percent"] or 0)) / 100)
                    )
                    planned_cost = int(planned_qty * Decimal(str(comp["unit_cost"])))

                    await conn.execute(
                        """
                        INSERT INTO production_order_materials (
                            production_order_id, product_id, planned_quantity,
                            unit, planned_cost
                        ) VALUES ($1, $2, $3, $4, $5)
                        """,
                        order_id,
                        comp["component_product_id"],
                        Decimal(str(round(planned_qty, 4))),
                        comp["unit"],
                        planned_cost,
                    )

                return {
                    "success": True,
                    "message": "Production order created",
                    "data": {"id": str(order_id), "order_number": order_number},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating production order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create production order")


@router.get("/{order_id}", response_model=ProductionOrderDetailResponse)
async def get_production_order(request: Request, order_id: UUID):
    """Get production order detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get order
            order = await conn.fetchrow(
                """
                SELECT po.*, p.nama_produk as product_name, p.sku as product_sku,
                       bom.bom_code, wc.name as work_center_name, w.name as warehouse_name,
                       wc.labor_rate_per_hour, wc.overhead_rate_per_hour
                FROM production_orders po
                JOIN products p ON p.id = po.product_id
                JOIN bill_of_materials bom ON bom.id = po.bom_id
                LEFT JOIN work_centers wc ON wc.id = po.work_center_id
                LEFT JOIN warehouses w ON w.id = po.warehouse_id
                WHERE po.tenant_id = $1 AND po.id = $2
                """,
                ctx["tenant_id"],
                order_id,
            )
            if not order:
                raise HTTPException(
                    status_code=404, detail="Production order not found"
                )

            # Get materials
            materials = await conn.fetch(
                """
                SELECT pom.*, p.nama_produk as product_name, p.sku as product_sku,
                       ib.batch_number
                FROM production_order_materials pom
                JOIN products p ON p.id = pom.product_id
                LEFT JOIN item_batches ib ON ib.id = pom.batch_id
                WHERE pom.production_order_id = $1
                ORDER BY p.nama_produk
                """,
                order_id,
            )

            # Get labor
            labor = await conn.fetch(
                """
                SELECT * FROM production_order_labor
                WHERE production_order_id = $1
                ORDER BY created_at
                """,
                order_id,
            )

            # Get completions
            completions = await conn.fetch(
                """
                SELECT pc.*, ib.batch_number
                FROM production_completions pc
                LEFT JOIN item_batches ib ON ib.id = pc.batch_id
                WHERE pc.production_order_id = $1
                ORDER BY pc.completion_date DESC
                """,
                order_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(order["id"]),
                    "order_number": order["order_number"],
                    "order_date": order["order_date"],
                    "product_id": str(order["product_id"]),
                    "product_name": order["product_name"],
                    "product_sku": order["product_sku"],
                    "bom_id": str(order["bom_id"]),
                    "bom_code": order["bom_code"],
                    "planned_quantity": order["planned_quantity"],
                    "completed_quantity": order["completed_quantity"],
                    "scrapped_quantity": order["scrapped_quantity"],
                    "unit": order["unit"],
                    "planned_start_date": order["planned_start_date"],
                    "planned_end_date": order["planned_end_date"],
                    "actual_start_date": order["actual_start_date"],
                    "actual_end_date": order["actual_end_date"],
                    "work_center_id": str(order["work_center_id"])
                    if order["work_center_id"]
                    else None,
                    "work_center_name": order["work_center_name"],
                    "labor_rate_per_hour": order["labor_rate_per_hour"],
                    "overhead_rate_per_hour": order["overhead_rate_per_hour"],
                    "warehouse_id": str(order["warehouse_id"])
                    if order["warehouse_id"]
                    else None,
                    "warehouse_name": order["warehouse_name"],
                    "sales_order_id": str(order["sales_order_id"])
                    if order["sales_order_id"]
                    else None,
                    "customer_id": str(order["customer_id"])
                    if order["customer_id"]
                    else None,
                    "planned_material_cost": order["planned_material_cost"],
                    "planned_labor_cost": order["planned_labor_cost"],
                    "planned_overhead_cost": order["planned_overhead_cost"],
                    "actual_material_cost": order["actual_material_cost"],
                    "actual_labor_cost": order["actual_labor_cost"],
                    "actual_overhead_cost": order["actual_overhead_cost"],
                    "variance_amount": order["variance_amount"],
                    "status": order["status"],
                    "priority": order["priority"],
                    "material_issue_journal_id": str(order["material_issue_journal_id"])
                    if order["material_issue_journal_id"]
                    else None,
                    "labor_journal_id": str(order["labor_journal_id"])
                    if order["labor_journal_id"]
                    else None,
                    "completion_journal_id": str(order["completion_journal_id"])
                    if order["completion_journal_id"]
                    else None,
                    "notes": order["notes"],
                    "materials": [
                        {
                            "id": str(m["id"]),
                            "product_id": str(m["product_id"]),
                            "product_name": m["product_name"],
                            "product_sku": m["product_sku"],
                            "planned_quantity": m["planned_quantity"],
                            "unit": m["unit"],
                            "planned_cost": m["planned_cost"],
                            "issued_quantity": m["issued_quantity"],
                            "actual_cost": m["actual_cost"],
                            "returned_quantity": m["returned_quantity"],
                            "variance_quantity": m["variance_quantity"],
                            "variance_cost": m["variance_cost"],
                            "batch_id": str(m["batch_id"]) if m["batch_id"] else None,
                            "batch_number": m["batch_number"],
                            "issued_date": m["issued_date"],
                            "warehouse_id": str(m["warehouse_id"])
                            if m["warehouse_id"]
                            else None,
                        }
                        for m in materials
                    ],
                    "labor": [
                        {
                            "id": str(lr["id"]),
                            "operation_id": str(lr["operation_id"])
                            if lr["operation_id"]
                            else None,
                            "operation_name": lr["operation_name"],
                            "planned_hours": lr["planned_hours"],
                            "planned_cost": lr["planned_cost"],
                            "actual_hours": lr["actual_hours"],
                            "actual_cost": lr["actual_cost"],
                            "worker_id": str(lr["worker_id"])
                            if lr["worker_id"]
                            else None,
                            "worker_name": lr["worker_name"],
                            "start_time": lr["start_time"],
                            "end_time": lr["end_time"],
                            "hourly_rate": lr["hourly_rate"],
                            "notes": lr["notes"],
                            "created_at": lr["created_at"],
                        }
                        for lr in labor
                    ],
                    "completions": [
                        {
                            "id": str(c["id"]),
                            "completion_date": c["completion_date"],
                            "good_quantity": c["good_quantity"],
                            "scrap_quantity": c["scrap_quantity"],
                            "quality_status": c["quality_status"],
                            "inspection_notes": c["inspection_notes"],
                            "unit_cost": c["unit_cost"],
                            "total_cost": c["total_cost"],
                            "warehouse_id": str(c["warehouse_id"])
                            if c["warehouse_id"]
                            else None,
                            "batch_id": str(c["batch_id"]) if c["batch_id"] else None,
                            "batch_number": c["batch_number"],
                            "journal_id": str(c["journal_id"])
                            if c["journal_id"]
                            else None,
                            "completed_by": str(c["completed_by"])
                            if c["completed_by"]
                            else None,
                            "created_at": c["created_at"],
                        }
                        for c in completions
                    ],
                    "created_at": order["created_at"],
                    "updated_at": order["updated_at"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting production order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get production order")


@router.patch("/{order_id}", response_model=ProductionResponse)
async def update_production_order(
    request: Request, order_id: UUID, body: UpdateProductionOrderRequest
):
    """Update production order (draft/planned only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                "SELECT status FROM production_orders WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"],
                order_id,
            )
            if not order:
                raise HTTPException(
                    status_code=404, detail="Production order not found"
                )

            if order["status"] not in ("draft", "planned"):
                raise HTTPException(
                    status_code=400, detail="Can only update draft or planned orders"
                )

            updates = []
            params = []
            param_idx = 1

            update_data = body.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes to update"}

            updates.append("updated_at = NOW()")
            params.extend([ctx["tenant_id"], order_id])

            await conn.execute(
                f"UPDATE production_orders SET {', '.join(updates)} WHERE tenant_id = ${param_idx} AND id = ${param_idx + 1}",
                *params,
            )

            return {"success": True, "message": "Production order updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating production order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update production order")


@router.delete("/{order_id}", response_model=ProductionResponse)
async def delete_production_order(request: Request, order_id: UUID):
    """Delete draft production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM production_orders WHERE tenant_id = $1 AND id = $2 AND status = 'draft'",
                ctx["tenant_id"],
                order_id,
            )
            if result == "DELETE 0":
                raise HTTPException(
                    status_code=400, detail="Order not found or not in draft status"
                )

            return {"success": True, "message": "Production order deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting production order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete production order")


# =============================================================================
# WORKFLOW
# =============================================================================
@router.post("/{order_id}/release", response_model=ProductionResponse)
async def release_order(request: Request, order_id: UUID):
    """Release order to production. Creates draft bills for subcontract operations."""
    try:
        ctx = get_user_context(request)
        tenant_id = ctx["tenant_id"]
        user_id = ctx.get("user_id")
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Advisory lock for subcontract release
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"SUBCONTRACT_RELEASE:{order_id}",
                )

                # Fetch order + validate
                order = await conn.fetchrow(
                    """
                    SELECT id, bom_id, planned_quantity, status
                    FROM production_orders
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    order_id,
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")
                if order["status"] not in ("draft", "planned"):
                    raise HTTPException(
                        status_code=400,
                        detail="Order cannot be released from current status",
                    )

                # Update status
                await conn.execute(
                    """
                    UPDATE production_orders
                    SET status = 'released', updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    order_id,
                )

                # Query BOM for subcontract operations
                bom_id = order["bom_id"]
                wo_qty = order["planned_quantity"] or Decimal("0")
                subcontract_ops = []
                if bom_id:
                    subcontract_ops = await conn.fetch(
                        """
                        SELECT bo.id, bo.operation_name, bo.subcontract_description,
                               bo.vendor_id, bo.subcontract_cost_per_unit,
                               v.name AS vendor_name
                        FROM bom_operations bo
                        LEFT JOIN vendors v ON v.id = bo.vendor_id AND v.tenant_id = $1
                        WHERE bo.bom_id = $2 AND bo.is_subcontract = true AND bo.vendor_id IS NOT NULL
                        """,
                        tenant_id,
                        bom_id,
                    )

                total_subcontract_cost = Decimal("0")

                for op in subcontract_ops:
                    unit_cost = op["subcontract_cost_per_unit"] or Decimal("0")
                    line_total = unit_cost * wo_qty

                    # Generate bill number
                    bill_number = await conn.fetchval(
                        "SELECT generate_bill_number($1, 'BILL')", tenant_id
                    )

                    # Create draft bill
                    bill_id = await conn.fetchval(
                        """
                        INSERT INTO bills (
                            tenant_id, invoice_number, vendor_id, vendor_name,
                            amount, amount_paid, issue_date, due_date, notes,
                            status, status_v2, subtotal, grand_total,
                            tax_rate, tax_inclusive, created_by
                        ) VALUES (
                            $1, $2, $3, $4,
                            $5, 0, CURRENT_DATE, CURRENT_DATE + INTERVAL '30 days', $6,
                            'draft', 'draft', $5, $5,
                            0, false, $7
                        ) RETURNING id
                        """,
                        tenant_id,
                        bill_number,
                        op["vendor_id"],
                        op["vendor_name"] or "Vendor",
                        line_total,
                        f"Subcontract: {op['operation_name']} for WO {order_id}",
                        user_id,
                    )

                    # Create bill item — purchase_account = WIP (1-10650) per Law 27
                    desc = (
                        op["subcontract_description"]
                        or op["operation_name"]
                        or "Subcontract service"
                    )
                    await conn.execute(
                        """
                        INSERT INTO bill_items (
                            bill_id, product_name, description, quantity, unit,
                            unit_price, discount_percent, discount_amount,
                            subtotal, total, line_number
                        ) VALUES (
                            $1, $2, $3, $4, 'unit',
                            $5, 0, 0,
                            $6, $6, 1
                        )
                        """,
                        bill_id,
                        op["operation_name"] or "Subcontract",
                        desc,
                        wo_qty,
                        unit_cost,
                        line_total,
                    )

                    # Create production_subcontracts record
                    await conn.execute(
                        """
                        INSERT INTO production_subcontracts (
                            tenant_id, production_order_id, bom_operation_id,
                            vendor_id, quantity, unit_cost, total_cost,
                            bill_id, bill_status, status
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'draft', 'pending')
                        """,
                        tenant_id,
                        order_id,
                        op["id"],
                        op["vendor_id"],
                        wo_qty,
                        unit_cost,
                        line_total,
                        bill_id,
                    )

                    total_subcontract_cost += line_total

                # Update subcontract_cost on production order
                if total_subcontract_cost > 0:
                    await conn.execute(
                        """
                        UPDATE production_orders
                        SET subcontract_cost = $1, total_cost = COALESCE(total_cost, 0) + $1,
                            updated_at = NOW()
                        WHERE tenant_id = $2 AND id = $3
                        """,
                        total_subcontract_cost,
                        tenant_id,
                        order_id,
                    )

                return {
                    "success": True,
                    "message": f"Production order released. {len(subcontract_ops)} subcontract bill(s) created.",
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error releasing order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to release order")


@router.post("/{order_id}/start", response_model=ProductionResponse)
async def start_production(request: Request, order_id: UUID):
    """Start production."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE production_orders
                SET status = 'in_progress', actual_start_date = CURRENT_DATE, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status = 'released'
                """,
                ctx["tenant_id"],
                order_id,
            )
            if result == "UPDATE 0":
                raise HTTPException(
                    status_code=400, detail="Order not found or not released"
                )

            return {"success": True, "message": "Production started"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting production: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start production")


@router.post("/{order_id}/complete", response_model=ProductionResponse)
async def complete_order(request: Request, order_id: UUID):
    """Complete production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    """
                    SELECT * FROM production_orders
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    ctx["tenant_id"],
                    order_id,
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                if order["status"] != "in_progress":
                    raise HTTPException(status_code=400, detail="Order not in progress")

                # Calculate variance
                actual_total = (
                    order["actual_material_cost"]
                    + order["actual_labor_cost"]
                    + order["actual_overhead_cost"]
                )
                planned_total = (
                    order["planned_material_cost"]
                    + order["planned_labor_cost"]
                    + order["planned_overhead_cost"]
                )
                variance = actual_total - planned_total

                await conn.execute(
                    """
                    UPDATE production_orders
                    SET status = 'completed', actual_end_date = CURRENT_DATE,
                        variance_amount = $3, updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    ctx["tenant_id"],
                    order_id,
                    variance,
                )

                # Bug #9 fix: Flush WIP residual via variance journal
                # Check WIP balance from this order's journals
                # Fase D3.3: role-based resolution (was 1-10650 / 5-90200).
                wip_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.WIP_GENERIC
                )
                variance_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.COGS_VARIANCE_PRODUCTION
                )

                if wip_account_id and variance_account_id:
                    # Get WIP balance from material issue + FG receipt journals of this order
                    wip_residual = await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE je.source_id = $1 AND je.tenant_id = $2
                          AND je.status = 'POSTED' AND jl.account_id = $3
                    """,
                        order_id,
                        ctx["tenant_id"],
                        wip_account_id,
                    )

                    if wip_residual and abs(float(wip_residual)) > Decimal("0.01"):
                        from datetime import date as _date_var
                        import uuid as _uuid_var

                        wip_residual = Decimal(str(wip_residual))

                        # Advisory lock
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtext($1))",
                            f"VARIANCE:{order_id}",
                        )

                        today_var = _date_var.today()
                        var_id = _uuid_var.uuid4()
                        ym_var = f"{today_var.year % 100:02d}{today_var.month:02d}"
                        # Self-healing canonical generator (V176): emits JV-VAR
                        # and bumps the JV-VAR counter (not the parent JV counter).
                        var_num = await conn.fetchval(
                            "SELECT get_next_journal_number($1, $2, $3)",
                            ctx["tenant_id"],
                            "JV-VAR",
                            today_var,
                        )

                        abs_amount = abs(wip_residual)

                        # Create DRAFT journal
                        await conn.execute(
                            """
                            INSERT INTO journal_entries (
                                id, tenant_id, journal_number, journal_date,
                                description, source_type, source_id,
                                total_debit, total_credit, status, created_by
                            ) VALUES ($1, $2, $3, $4, $5, 'PRODUCTION_VARIANCE', $6, $7, $7, 'DRAFT', $8)
                        """,
                            var_id,
                            ctx["tenant_id"],
                            var_num,
                            today_var,
                            f"Manufacturing variance WO {order['order_number']}",
                            order_id,
                            abs_amount,
                            ctx.get("user_id"),
                        )

                        if wip_residual > 0:
                            # WIP has debit residual: Cr WIP, Dr Variance Expense
                            await conn.execute(
                                """
                                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, memo, line_number)
                                VALUES (gen_random_uuid(), $1, $2, $3, 0, 'Variance expense', 1)
                            """,
                                var_id,
                                variance_account_id,
                                abs_amount,
                            )
                            await conn.execute(
                                """
                                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, memo, line_number)
                                VALUES (gen_random_uuid(), $1, $2, 0, $3, 'WIP flush', 2)
                            """,
                                var_id,
                                wip_account_id,
                                abs_amount,
                            )
                        else:
                            # WIP has credit residual: Dr WIP, Cr Variance Income
                            await conn.execute(
                                """
                                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, memo, line_number)
                                VALUES (gen_random_uuid(), $1, $2, $3, 0, 'WIP flush', 1)
                            """,
                                var_id,
                                wip_account_id,
                                abs_amount,
                            )
                            await conn.execute(
                                """
                                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, memo, line_number)
                                VALUES (gen_random_uuid(), $1, $2, 0, $3, 'Variance credit', 2)
                            """,
                                var_id,
                                variance_account_id,
                                abs_amount,
                            )

                        # DRAFT -> POSTED (Law 20)
                        await conn.execute(
                            """
                            UPDATE journal_entries SET status = 'POSTED' WHERE id = $1
                        """,
                            var_id,
                        )

                        logger.info(
                            f"Variance journal {var_num}: WIP flush {wip_residual} for {order['order_number']}"
                        )

                return {
                    "success": True,
                    "message": "Production order completed",
                    "data": {"variance_amount": variance},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to complete order")


async def _reverse_journal(
    conn, tenant_id: str, user_id, original_journal_id, reason: str
):
    """Create a reversal journal (Law 2 + Law 26): swap debit/credit of original, link via reversal_of_id."""
    import uuid as _uuid_rev
    from datetime import date as _date_rev

    original = await conn.fetchrow(
        "SELECT id, journal_number, source_type, source_id, total_debit, total_credit, status, reversed_by_id, journal_date, period_id FROM journal_entries WHERE id = $1 AND tenant_id = $2",
        original_journal_id,
        tenant_id,
    )
    if not original:
        return None
    if original["status"] != "POSTED":
        return None
    if original["reversed_by_id"]:
        return None  # already reversed (Law 26 single-reversal)

    # --- Determine a reversal date that lands in an OPEN period (Law 5) ----
    # Same-period reversal (original journal_date) is the accounting-correct
    # default; fall back to today; if BOTH are closed/locked raise a clean
    # HTTPException(400) so callers surface a proper error (not a raw 500 from
    # the prevent_closed_period_journal trigger). Inline fiscal_periods status
    # check mirrors record_labor's posting_date gate.
    async def _period_is_open(d):
        fp_chk = await conn.fetchrow(
            """
            SELECT status FROM fiscal_periods
            WHERE tenant_id = $1
              AND $2 BETWEEN start_date AND end_date
            LIMIT 1
            """,
            tenant_id,
            d,
        )
        # No matching period or CLOSED/LOCKED => treated as not-open.
        return bool(fp_chk) and str(fp_chk["status"]).upper() not in (
            "CLOSED",
            "LOCKED",
        )

    orig_date = original["journal_date"]
    today_actual = _date_rev.today()
    if orig_date is not None and await _period_is_open(orig_date):
        reversal_date = orig_date
    elif await _period_is_open(today_actual):
        reversal_date = today_actual
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Tidak bisa reverse: periode jurnal asal dan hari ini "
                "sama-sama tertutup/terkunci."
            ),
        )

    ym_rev = f"{reversal_date.year % 100:02d}{reversal_date.month:02d}"
    # Self-healing canonical generator (V176): plain JV, reversal date.
    rev_num = await conn.fetchval(
        "SELECT get_next_journal_number($1, $2, $3)",
        tenant_id,
        "JV",
        reversal_date,
    )
    rev_id = _uuid_rev.uuid4()
    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id,
            total_debit, total_credit, status, created_by,
            reversal_of_id, reversal_reason, period_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, 'DRAFT', $9, $10, $11, $12)
        """,
        rev_id,
        tenant_id,
        rev_num,
        reversal_date,
        f"REVERSAL {original['journal_number']}: {reason}",
        original["source_type"],
        original["source_id"],
        original["total_debit"],
        user_id,
        original_journal_id,
        reason,
        original[
            "period_id"
        ],  # inherit period from original (carry period_id on reversal)
    )
    # Copy lines with debit/credit swapped
    orig_lines = await conn.fetch(
        "SELECT account_id, debit, credit, memo, item_id FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
        original_journal_id,
    )
    for idx, ln in enumerate(orig_lines, start=1):
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo, item_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            _uuid_rev.uuid4(),
            rev_id,
            idx,
            ln["account_id"],
            ln["credit"],
            ln["debit"],  # SWAPPED
            f"REV: {ln['memo']}" if ln["memo"] else None,
            ln["item_id"],
        )
    # Law 20: POST reversal (hash chain)
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", rev_id
    )
    # Mark original as reversed
    await conn.execute(
        "UPDATE journal_entries SET reversed_by_id = $1, reversed_at = NOW() WHERE id = $2",
        rev_id,
        original_journal_id,
    )
    return rev_id


async def _reverse_inventory_ledger(
    conn,
    tenant_id: str,
    user_id,
    source_type: str,
    source_id,
    reversal_journal_id,
    movement_tag: str,
):
    """Insert reversal rows for all inventory_ledger entries of a given source.
    Flips quantity_in/out, recomputes balance, sets movement_type to {TAG}_REVERSAL, links new journal."""
    rows = await conn.fetch(
        """
        SELECT id, product_id, product_code, product_name, warehouse_id, source_number,
               quantity_in, quantity_out, unit_cost, total_cost
        FROM inventory_ledger
        WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3
          AND movement_type NOT LIKE '%_REVERSAL'
        """,
        tenant_id,
        source_type,
        source_id,
    )
    for r in rows:
        current_bal = await conn.fetchval(
            "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id=$1 AND product_id=$2",
            tenant_id,
            r["product_id"],
        )
        # Swap: original in -> out, original out -> in
        new_in = r["quantity_out"]
        new_out = r["quantity_in"]
        new_bal = (
            Decimal(str(current_bal)) + Decimal(str(new_in)) - Decimal(str(new_out))
        )
        await conn.execute(
            """
            INSERT INTO inventory_ledger (
                id, tenant_id, product_id, product_code, product_name,
                movement_type, movement_date, source_type, source_id, source_number,
                quantity_in, quantity_out, quantity_balance,
                unit_cost, total_cost, average_cost,
                warehouse_id, created_by, notes, journal_id
            ) VALUES (
                gen_random_uuid(), $1, $2, $3, $4,
                $5, CURRENT_DATE, $6, $7, $8,
                $9, $10, $11,
                $12, $13, $12,
                $14, $15, $16, $17
            )
            """,
            tenant_id,
            r["product_id"],
            r["product_code"],
            r["product_name"],
            f"{movement_tag}_REVERSAL",
            source_type,
            source_id,
            r["source_number"],
            new_in,
            new_out,
            new_bal,
            r["unit_cost"],
            r["total_cost"],
            r["warehouse_id"],
            user_id,
            "Reversal of production cancellation",
            reversal_journal_id,
        )


@router.post("/{order_id}/cancel", response_model=ProductionResponse)
async def cancel_order(request: Request, order_id: UUID):
    """Cancel production order. Reverses Material Issue + FG Receipt journals and ledger if present."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        await _ensure_production_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"PRODUCTION_CANCEL:{order_id}",
                )

                order = await conn.fetchrow(
                    "SELECT * FROM production_orders WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"],
                    order_id,
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")
                if order["status"] == "cancelled":
                    raise HTTPException(
                        status_code=400, detail="Order already cancelled"
                    )

                # 1. Reverse FG Receipt first (un-credit WIP, un-debit FG)
                if order["completion_journal_id"]:
                    rev_fg_id = await _reverse_journal(
                        conn,
                        ctx["tenant_id"],
                        ctx["user_id"],
                        order["completion_journal_id"],
                        "Production order cancelled",
                    )
                    if rev_fg_id:
                        await _reverse_inventory_ledger(
                            conn,
                            ctx["tenant_id"],
                            ctx["user_id"],
                            "PRODUCTION_OUTPUT",
                            order_id,
                            rev_fg_id,
                            "PRODUCTION_OUTPUT",
                        )

                # 2. Reverse Material Issue (un-debit WIP, un-credit Persediaan RM)
                if order["material_issue_journal_id"]:
                    rev_mi_id = await _reverse_journal(
                        conn,
                        ctx["tenant_id"],
                        ctx["user_id"],
                        order["material_issue_journal_id"],
                        "Production order cancelled",
                    )
                    if rev_mi_id:
                        await _reverse_inventory_ledger(
                            conn,
                            ctx["tenant_id"],
                            ctx["user_id"],
                            "MATERIAL_ISSUE",
                            order_id,
                            rev_mi_id,
                            "MATERIAL_ISSUE",
                        )

                # 3. Reset WO counters (cumulative fields set to 0; original rows preserved for audit)
                await conn.execute(
                    """
                    UPDATE production_orders
                    SET status = 'cancelled',
                        completed_quantity = 0,
                        scrapped_quantity = 0,
                        actual_material_cost = 0,
                        actual_labor_cost = 0,
                        actual_overhead_cost = 0,
                        total_cost = 0,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    order_id,
                )

                # Cascade: clean up subcontract bills
                subcontracts = await conn.fetch(
                    "SELECT * FROM production_subcontracts WHERE production_order_id = $1 AND tenant_id = $2 AND status != 'voided'",
                    order_id,
                    ctx["tenant_id"],
                )
                for sc in subcontracts:
                    if sc["bill_id"]:
                        bill = await conn.fetchrow(
                            "SELECT status_v2 FROM bills WHERE id = $1 AND tenant_id = $2",
                            sc["bill_id"],
                            ctx["tenant_id"],
                        )
                        if bill and bill["status_v2"] == "draft":
                            await conn.execute(
                                "UPDATE production_subcontracts SET bill_id = NULL, status = 'voided', updated_at = NOW() WHERE id = $1",
                                sc["id"],
                            )
                            await conn.execute(
                                "DELETE FROM bill_items WHERE bill_id = $1",
                                sc["bill_id"],
                            )
                            await conn.execute(
                                "DELETE FROM bills WHERE id = $1", sc["bill_id"]
                            )
                            continue
                    await conn.execute(
                        "UPDATE production_subcontracts SET status = 'voided', updated_at = NOW() WHERE id = $1",
                        sc["id"],
                    )

                return {"success": True, "message": "Production order cancelled"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel order")


# =============================================================================
# MATERIAL ISSUE
# =============================================================================
@router.post("/{order_id}/issue-materials", response_model=ProductionResponse)
async def issue_materials(
    request: Request, order_id: UUID, materials: List[ProductionMaterialInput]
):
    """Issue materials to production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        await _ensure_production_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM production_orders WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"],
                    order_id,
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                if order["status"] not in ("released", "in_progress"):
                    raise HTTPException(
                        status_code=400, detail="Order must be released or in progress"
                    )

                # Law 13: Advisory lock for atomic material issue
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"MATERIAL_ISSUE:{order_id}",
                )

                # Resolve order number for journal description
                order_number = order["order_number"]

                total_issued_cost = Decimal("0")
                ledger_rows: list = []  # collected for ledger insert after journal creation

                for mat in materials:
                    # Product master info (name, code, default warehouse)
                    product = await conn.fetchrow(
                        """
                        SELECT id, item_code, nama_produk, purchase_price, track_inventory,
                               warehouse_id AS default_warehouse_id
                        FROM products WHERE tenant_id = $1 AND id = $2
                        """,
                        ctx["tenant_id"],
                        mat.product_id,
                    )
                    if not product:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Product {mat.product_id} not found",
                        )

                    # Law/Inventory Rule 3: use WAC from inventory_ledger; fallback to purchase_price
                    wac = await conn.fetchval(
                        "SELECT get_weighted_average_cost($1, $2)",
                        ctx["tenant_id"],
                        mat.product_id,
                    )
                    unit_cost = Decimal(str(wac or 0))
                    if unit_cost <= 0:
                        unit_cost = Decimal(str(product["purchase_price"] or 0))
                    if unit_cost <= 0 and product["track_inventory"]:
                        raise HTTPException(
                            status_code=409,
                            detail=f"Tidak bisa issue material: produk '{product['nama_produk']}' tidak punya riwayat stok / biaya perolehan. "
                            f"Catat penerimaan stok atau opening balance terlebih dahulu.",
                        )

                    qty_dec = Decimal(str(mat.quantity))
                    issue_cost = (qty_dec * unit_cost).quantize(Decimal("0.01"))

                    # Bug #3 fix: stock validation before issue
                    wh_id = (
                        mat.warehouse_id
                        or order["warehouse_id"]
                        or product["default_warehouse_id"]
                    )
                    current_stock = await conn.fetchval(
                        "SELECT COALESCE(quantity, 0) FROM warehouse_stock WHERE item_id = $1 AND warehouse_id = $2 AND tenant_id = $3",
                        str(product["id"]),
                        str(wh_id),
                        ctx["tenant_id"],
                    )
                    if current_stock is None:
                        current_stock = Decimal("0")
                    if Decimal(str(current_stock)) < qty_dec:
                        raise HTTPException(
                            status_code=409,
                            detail=f"Stok {product['nama_produk']} tidak cukup di gudang. Tersedia: {current_stock}, dibutuhkan: {qty_dec}",
                        )

                    # Get planned material
                    planned = await conn.fetchrow(
                        """
                        SELECT * FROM production_order_materials
                        WHERE production_order_id = $1 AND product_id = $2
                        """,
                        order_id,
                        mat.product_id,
                    )

                    if planned:
                        await conn.execute(
                            """
                            UPDATE production_order_materials
                            SET issued_quantity = issued_quantity + $3,
                                actual_cost = actual_cost + $4,
                                issued_date = CURRENT_DATE,
                                issued_by = $5,
                                warehouse_id = $6,
                                batch_id = $7
                            WHERE production_order_id = $1 AND product_id = $2
                            """,
                            order_id,
                            mat.product_id,
                            mat.quantity,
                            issue_cost,
                            ctx["user_id"],
                            mat.warehouse_id,
                            mat.batch_id,
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO production_order_materials (
                                production_order_id, product_id, planned_quantity, unit,
                                issued_quantity, actual_cost, issued_date, issued_by, warehouse_id, batch_id
                            ) VALUES ($1, $2, 0, $3, $4, $5, CURRENT_DATE, $6, $7, $8)
                            """,
                            order_id,
                            mat.product_id,
                            mat.unit,
                            mat.quantity,
                            issue_cost,
                            ctx["user_id"],
                            mat.warehouse_id,
                            mat.batch_id,
                        )

                    total_issued_cost += issue_cost
                    ledger_rows.append(
                        {
                            "product_id": mat.product_id,
                            "product_code": product["item_code"],
                            "product_name": product["nama_produk"],
                            "warehouse_id": mat.warehouse_id
                            or order["warehouse_id"]
                            or product["default_warehouse_id"],
                            "quantity": qty_dec,
                            "unit_cost": unit_cost,
                            "total_cost": issue_cost,
                        }
                    )

                # Update order actual material cost
                await conn.execute(
                    """
                    UPDATE production_orders
                    SET actual_material_cost = actual_material_cost + $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    order_id,
                    total_issued_cost,
                )

                # =============================================================
                # JOURNAL Dr 1-10650 WIP / Cr 1-10600 Persediaan RM (per material)
                # Law 6: source_type='MATERIAL_ISSUE', source_id=order_id
                # Law 20: DRAFT -> POSTED (hash chain)
                # Law 27: resolve_account_id (no hardcoded CoA)
                # =============================================================
                mi_journal_id = None
                if total_issued_cost > 0:
                    import uuid as _uuid_mi

                    # Fase D3.3: role-based resolution (was 1-10650 / 1-10600).
                    wip_acct = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.WIP_GENERIC
                    )
                    inv_acct = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.INVENTORY_MERCHANDISE
                    )
                    if not wip_acct or not inv_acct:
                        raise HTTPException(
                            status_code=500,
                            detail="Akun WIP_GENERIC atau INVENTORY_MERCHANDISE tidak ter-resolve",
                        )

                    from datetime import date as _date

                    # Optional period-gated posting date (default = today). Must
                    # fall in an OPEN fiscal period. Clean HTTP 400 pre-check
                    # BEFORE any journal/ledger insert (the
                    # prevent_closed_period_journal trigger would otherwise surface
                    # a raw DB error). Iron Law 5. Mirrors record_labor /
                    # report_output posting_date gate. posting_date is taken from
                    # the first material row (single journal per request).
                    effective_date = (
                        materials[0].posting_date if materials else None
                    ) or _date.today()
                    fp_mi = await conn.fetchrow(
                        """
                        SELECT status FROM fiscal_periods
                        WHERE tenant_id = $1
                          AND $2 BETWEEN start_date AND end_date
                        LIMIT 1
                        """,
                        ctx["tenant_id"],
                        effective_date,
                    )
                    if (not fp_mi) or str(fp_mi["status"]).upper() in (
                        "CLOSED",
                        "LOCKED",
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Tidak bisa issue material: tanggal {effective_date} "
                                f"berada di periode yang sudah ditutup/dikunci atau "
                                f"tidak ada periode aktif. Pilih tanggal di periode "
                                f"yang masih OPEN."
                            ),
                        )

                    today = effective_date
                    year_month_str = f"{today.year % 100:02d}{today.month:02d}"
                    # Self-healing canonical generator (V176): plain JV, effective date.
                    jnum = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "JV",
                        today,
                    )
                    mi_journal_id = _uuid_mi.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id,
                            total_debit, total_credit, status, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'MATERIAL_ISSUE', $6, $7, $7, 'DRAFT', $8)
                        """,
                        mi_journal_id,
                        ctx["tenant_id"],
                        jnum,
                        today,
                        f"Pengeluaran Bahan {order_number}",
                        order_id,
                        total_issued_cost,
                        ctx["user_id"],
                    )
                    # Line 1: Dr WIP total
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        _uuid_mi.uuid4(),
                        mi_journal_id,
                        wip_acct,
                        total_issued_cost,
                        f"WIP {order_number}",
                    )
                    # Lines 2..N: Cr Persediaan per material
                    for idx, row in enumerate(ledger_rows, start=2):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo, item_id)
                            VALUES ($1, $2, $3, $4, 0, $5, $6, $7)
                            """,
                            _uuid_mi.uuid4(),
                            mi_journal_id,
                            idx,
                            inv_acct,
                            row["total_cost"],
                            f"Persediaan {row['product_name']} - {order_number}",
                            row["product_id"],
                        )
                    # Law 20: POST
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        mi_journal_id,
                    )

                    # Insert inventory_ledger OUT per material (Inventory Rule 1: atomic with journal)
                    for row in ledger_rows:
                        # Current balance
                        current_bal = await conn.fetchval(
                            "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id=$1 AND product_id=$2",
                            ctx["tenant_id"],
                            row["product_id"],
                        )
                        new_bal = Decimal(str(current_bal)) - row["quantity"]
                        avg_snap = (
                            await conn.fetchval(
                                "SELECT get_weighted_average_cost($1, $2)",
                                ctx["tenant_id"],
                                row["product_id"],
                            )
                            or row["unit_cost"]
                        )
                        await conn.execute(
                            """
                            INSERT INTO inventory_ledger (
                                id, tenant_id, product_id, product_code, product_name,
                                movement_type, movement_date, source_type, source_id, source_number,
                                quantity_in, quantity_out, quantity_balance,
                                unit_cost, total_cost, average_cost,
                                warehouse_id, created_by, notes, journal_id
                            ) VALUES (
                                gen_random_uuid(), $1, $2, $3, $4,
                                'MATERIAL_ISSUE', $16, 'MATERIAL_ISSUE', $5, $6,
                                0, $7, $8,
                                $9, $10, $11,
                                $12, $13, $14, $15
                            )
                            """,
                            ctx["tenant_id"],
                            row["product_id"],
                            row["product_code"],
                            row["product_name"],
                            order_id,
                            order_number,
                            row["quantity"],
                            new_bal,
                            row["unit_cost"],
                            row["total_cost"],
                            avg_snap,
                            row["warehouse_id"],
                            ctx["user_id"],
                            f"Pengeluaran bahan untuk {order_number}",
                            mi_journal_id,
                            effective_date,
                        )

                    # Link journal to WO
                    await conn.execute(
                        "UPDATE production_orders SET material_issue_journal_id = $1 WHERE id = $2",
                        mi_journal_id,
                        order_id,
                    )

                return {
                    "success": True,
                    "message": f"Materials issued, total cost: {total_issued_cost}",
                    "data": {
                        "journal_id": str(mi_journal_id) if mi_journal_id else None
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error issuing materials: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to issue materials")


# =============================================================================
# LABOR
# =============================================================================
@router.post("/{order_id}/labor", response_model=ProductionResponse)
async def record_labor(request: Request, order_id: UUID, body: ProductionLaborInput):
    """Record labor for production order.

    V173 deep-val 2.5 — standard-cost labor + auto-applied overhead.
      - Standard labor cost = actual_hours × work_centers.labor_rate_per_hour
      - Standard OH cost    = actual_hours × work_centers.overhead_rate_per_hour
      - body.hourly_rate is preserved in production_order_labor for audit trail
        but is NOT used for accounting posting (standard cost only).
      - Two journals are posted (when respective rate > 0):
          PRODUCTION_LABOR    : Dr WIP_GENERIC / Cr MFG_LABOR_APPLIED (2-10430)
          PRODUCTION_OVERHEAD : Dr WIP_GENERIC / Cr MFG_OVERHEAD_APPLIED (2-10440)
      - PRO-D-2 fix: handler body wrapped in conn.transaction() — partial-post
        on exception was historically possible.
    """
    try:
        import uuid as _uuid_lb
        from datetime import date as _date_lb

        ctx = get_user_context(request)
        pool = await get_pool()
        await _ensure_production_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # PRO-D-2 advisory lock at TOP of tx (Surprise #28 pattern)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"LABOR:{order_id}",
                )

                order = await conn.fetchrow(
                    """
                    SELECT po.*, wc.labor_rate_per_hour, wc.overhead_rate_per_hour
                    FROM production_orders po
                    LEFT JOIN work_centers wc ON wc.id = po.work_center_id
                    WHERE po.tenant_id = $1 AND po.id = $2
                    """,
                    ctx["tenant_id"],
                    order_id,
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                if order["status"] not in ("released", "in_progress"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Tidak bisa mencatat labor: WO berstatus '{order['status']}'. Labor hanya untuk WO yang sudah dirilis atau sedang dikerjakan (released/in_progress).",
                    )
                if (
                    order["completed_quantity"] is not None
                    and order["planned_quantity"] is not None
                    and order["completed_quantity"] >= order["planned_quantity"]
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Tidak bisa mencatat labor: output WO sudah penuh (selesai dilaporkan). Catat labor SEBELUM menyelesaikan output; biaya labor setelah output penuh akan terdampar di WIP.",
                    )

                # Standard rates from work_centers (NULL-safe)
                labor_rate_std = Decimal(str(order["labor_rate_per_hour"] or 0))
                oh_rate_std = Decimal(str(order["overhead_rate_per_hour"] or 0))
                actual_hours = Decimal(str(body.actual_hours))

                labor_cost_applied = (actual_hours * labor_rate_std).quantize(
                    Decimal("0.01")
                )
                oh_cost_applied = (actual_hours * oh_rate_std).quantize(Decimal("0.01"))

                # Audit-trail input (body.hourly_rate × hours) — stored in
                # production_order_labor.actual_cost so legacy reports stay
                # consistent. Journals use standard cost only.
                audit_cost = int(actual_hours * Decimal(str(body.hourly_rate)))

                labor_id = await conn.fetchval(
                    """
                    INSERT INTO production_order_labor (
                        production_order_id, operation_id, operation_name,
                        actual_hours, actual_cost, worker_id, worker_name,
                        start_time, end_time, hourly_rate, notes
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    order_id,
                    body.operation_id,
                    body.operation_name,
                    body.actual_hours,
                    int(labor_cost_applied),
                    body.worker_id,
                    body.worker_name,
                    body.start_time,
                    body.end_time,
                    body.hourly_rate,
                    body.notes,
                )

                # Update production_orders standard-applied amounts
                await conn.execute(
                    """
                    UPDATE production_orders
                    SET actual_labor_cost    = COALESCE(actual_labor_cost, 0) + $2,
                        actual_overhead_cost = COALESCE(actual_overhead_cost, 0) + $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    order_id,
                    labor_cost_applied,
                    oh_cost_applied,
                )

                # Optional period-gated posting date (default = today). Must
                # fall in an OPEN fiscal period. Clean HTTP 400 pre-check BEFORE
                # any journal insert (the prevent_closed_period_journal trigger
                # would otherwise surface a raw DB error). Iron Law 5.
                effective_date = body.posting_date or _date_lb.today()
                fp_lb = await conn.fetchrow(
                    """
                    SELECT status FROM fiscal_periods
                    WHERE tenant_id = $1
                      AND $2 BETWEEN start_date AND end_date
                    LIMIT 1
                    """,
                    ctx["tenant_id"],
                    effective_date,
                )
                if (not fp_lb) or str(fp_lb["status"]).upper() in ("CLOSED", "LOCKED"):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Tidak bisa mencatat labor: tanggal {effective_date} "
                            f"berada di periode yang sudah ditutup/dikunci atau "
                            f"tidak ada periode aktif. Pilih tanggal di periode "
                            f"yang masih OPEN."
                        ),
                    )

                today_lb = effective_date
                ym_lb = f"{today_lb.year % 100:02d}{today_lb.month:02d}"
                order_number = order["order_number"]

                # ---- Journal #1 : PRODUCTION_LABOR -------------------------
                if labor_cost_applied > 0:
                    wip_id = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.WIP_GENERIC
                    )
                    labor_applied_id = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.MFG_LABOR_APPLIED
                    )

                    # Self-healing canonical generator (V176): emits JV-LB, bumps JV-LB counter.
                    jnum_lb = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "JV-LB",
                        today_lb,
                    )
                    je_lb = _uuid_lb.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id,
                            total_debit, total_credit, status, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'PRODUCTION_LABOR', $6, $7, $7, 'DRAFT', $8)
                        """,
                        je_lb,
                        ctx["tenant_id"],
                        jnum_lb,
                        today_lb,
                        f"Labor applied {actual_hours}h x {labor_rate_std}/h ({order_number})",
                        order_id,
                        labor_cost_applied,
                        ctx.get("user_id"),
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        _uuid_lb.uuid4(),
                        je_lb,
                        wip_id,
                        labor_cost_applied,
                        f"WIP labor applied {order_number}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        _uuid_lb.uuid4(),
                        je_lb,
                        labor_applied_id,
                        labor_cost_applied,
                        f"MFG_LABOR_APPLIED {order_number}",
                    )
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        je_lb,
                    )

                # ---- Journal #2 : PRODUCTION_OVERHEAD ----------------------
                if oh_cost_applied > 0:
                    wip_id = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.WIP_GENERIC
                    )
                    oh_applied_id = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.MFG_OVERHEAD_APPLIED
                    )

                    # Self-healing canonical generator (V176): emits JV-OH, bumps JV-OH counter.
                    jnum_oh = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "JV-OH",
                        today_lb,
                    )
                    je_oh = _uuid_lb.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id,
                            total_debit, total_credit, status, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'PRODUCTION_OVERHEAD', $6, $7, $7, 'DRAFT', $8)
                        """,
                        je_oh,
                        ctx["tenant_id"],
                        jnum_oh,
                        today_lb,
                        f"OH applied {actual_hours}h x {oh_rate_std}/h ({order_number})",
                        order_id,
                        oh_cost_applied,
                        ctx.get("user_id"),
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        _uuid_lb.uuid4(),
                        je_oh,
                        wip_id,
                        oh_cost_applied,
                        f"WIP overhead applied {order_number}",
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        _uuid_lb.uuid4(),
                        je_oh,
                        oh_applied_id,
                        oh_cost_applied,
                        f"MFG_OVERHEAD_APPLIED {order_number}",
                    )
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        je_oh,
                    )

                return {
                    "success": True,
                    "message": "Labor recorded (standard-cost applied)",
                    "data": {
                        "id": str(labor_id),
                        "actual_hours": str(actual_hours),
                        "labor_cost_applied": str(labor_cost_applied),
                        "overhead_cost_applied": str(oh_cost_applied),
                        "labor_rate_std": str(labor_rate_std),
                        "overhead_rate_std": str(oh_rate_std),
                        "audit_input_cost": audit_cost,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording labor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to record labor")


# =============================================================================
# OUTPUT / COMPLETION
# =============================================================================
@router.post("/{order_id}/report-output", response_model=ProductionResponse)
async def report_output(
    request: Request, order_id: UUID, body: ProductionCompletionInput
):
    """Report production output."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        await _ensure_production_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM production_orders WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"],
                    order_id,
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                if order["status"] not in ("released", "in_progress"):
                    raise HTTPException(
                        status_code=400, detail="Order must be released or in progress"
                    )

                # BUG-1C-3 / Surprise #28 fix: advisory lock hoisted to top of tx.
                # Serializes concurrent retries of report_output for same WO so that
                # over-output guard, completion INSERT, WO counter UPDATE, and FG
                # receipt journal emission all run under a single critical section.
                # Previously the lock was acquired AFTER completion INSERT (~L2031),
                # allowing two parallel retries to both pass the overrun guard
                # (each seeing identical existing_completed) and both proceed.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"PRODUCTION_OUTPUT:{order_id}",
                )

                # Over-output guard: warn if good+scrap > remaining, require allow_overrun=true
                existing_totals = await conn.fetchrow(
                    """
                    SELECT COALESCE(SUM(good_quantity), 0) as total_good,
                           COALESCE(SUM(scrap_quantity), 0) as total_scrap
                    FROM production_completions WHERE production_order_id = $1
                """,
                    order_id,
                )
                existing_completed = Decimal(
                    str(existing_totals["total_good"])
                ) + Decimal(str(existing_totals["total_scrap"]))
                planned = Decimal(str(order["planned_quantity"] or order["quantity"]))
                remaining_output = planned - existing_completed
                requested = Decimal(str(body.good_quantity)) + Decimal(
                    str(body.scrap_quantity or 0)
                )
                is_overrun = requested > remaining_output
                if is_overrun and not body.allow_overrun:
                    overrun_amount = requested - remaining_output
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "Output melebihi target",
                            "error_code": "OVERRUN_REQUIRES_CONFIRMATION",
                            "planned": str(planned),
                            "existing": str(existing_completed),
                            "remaining": str(max(Decimal("0"), remaining_output)),
                            "requested": str(requested),
                            "overrun": str(overrun_amount),
                            "message": f"Target {planned} pcs, sudah {existing_completed} pcs. Mencoba tambah {requested} pcs (kelebihan {overrun_amount} pcs). Konfirmasi untuk lanjutkan.",
                        },
                    )
                if is_overrun:
                    logger.info(
                        f"WO {order['order_number']}: overrun {requested - remaining_output} pcs (confirmed by user {ctx['user_id']})"
                    )

                # Calculate unit cost (include subcontract cost)
                subcontract_cost = Decimal(str(order["subcontract_cost"] or 0))
                # Bug #5 fix: block FG receipt if subcontract bill not posted
                if subcontract_cost > 0:
                    unposted = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM production_subcontracts ps
                        JOIN bills b ON b.id = ps.bill_id
                        WHERE ps.production_order_id = $1 AND ps.tenant_id = $2
                          AND (b.status IS NULL OR b.status != 'posted')
                    """,
                        str(order_id),
                        ctx["tenant_id"],
                    )
                    if unposted and unposted > 0:
                        raise HTTPException(
                            status_code=409,
                            detail="Faktur subkontrak harus di-posting sebelum lapor output. Posting faktur pembelian terlebih dahulu.",
                        )
                total_actual = (
                    order["actual_material_cost"]
                    + order["actual_labor_cost"]
                    + order["actual_overhead_cost"]
                    + subcontract_cost
                )
                total_qty = Decimal(str(order["completed_quantity"])) + Decimal(
                    str(body.good_quantity)
                )
                unit_cost = int(total_actual / total_qty) if total_qty > 0 else 0
                total_cost = int(unit_cost * Decimal(str(body.good_quantity)))

                # Record completion
                completion_id = await conn.fetchval(
                    """
                    INSERT INTO production_completions (
                        production_order_id, completion_date, good_quantity,
                        scrap_quantity, quality_status, inspection_notes,
                        unit_cost, total_cost, warehouse_id, batch_id, completed_by, is_overrun
                    ) VALUES ($1, CURRENT_DATE, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    order_id,
                    body.good_quantity,
                    body.scrap_quantity,
                    body.quality_status,
                    body.inspection_notes,
                    unit_cost,
                    total_cost,
                    body.warehouse_id,
                    body.batch_id,
                    ctx["user_id"],
                    is_overrun,
                )

                # Update order quantities
                await conn.execute(
                    """
                    UPDATE production_orders
                    SET completed_quantity = completed_quantity + $2,
                        scrapped_quantity = scrapped_quantity + $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    order_id,
                    body.good_quantity,
                    body.scrap_quantity,
                )

                # Update total_cost to reflect all cost components
                await conn.execute(
                    """UPDATE production_orders
                       SET total_cost = actual_material_cost + actual_labor_cost + actual_overhead_cost + COALESCE(subcontract_cost, 0)
                       WHERE id = $1""",
                    order_id,
                )

                # =============================================================
                # FG RECEIPT JOURNAL Dr 1-10600 Persediaan FG / Cr 1-10650 WIP
                # + inventory_ledger PRODUCTION_OUTPUT for FG product
                # Law 6, 13, 20, 25, 27 / Inventory Rules 1, 3, 10
                # Advisory lock hoisted to top of tx (see Surprise #28 fix above).
                # =============================================================

                fg_journal_id = None
                if total_cost > 0:
                    import uuid as _uuid_ro

                    # Resolve FG product info
                    fg_product = await conn.fetchrow(
                        """
                        SELECT item_code, nama_produk, warehouse_id AS default_warehouse_id
                        FROM products WHERE tenant_id = $1 AND id = $2
                        """,
                        ctx["tenant_id"],
                        order["product_id"],
                    )
                    if not fg_product:
                        raise HTTPException(
                            status_code=404, detail="FG product not found"
                        )

                    # Fase D3.3: role-based resolution (was 1-10600 / 1-10650).
                    # FG kept on INVENTORY_MERCHANDISE per owner decision.
                    fg_acct = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.INVENTORY_MERCHANDISE
                    )
                    wip_acct = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.WIP_GENERIC
                    )
                    if not fg_acct or not wip_acct:
                        raise HTTPException(
                            status_code=500,
                            detail="Akun INVENTORY_MERCHANDISE (FG) atau WIP_GENERIC tidak ter-resolve",
                        )

                    from datetime import date as _date_ro

                    # Optional period-gated posting date (default = today). Must
                    # fall in an OPEN fiscal period. Clean HTTP 400 pre-check
                    # BEFORE any journal insert (the prevent_closed_period_journal
                    # trigger would otherwise surface a raw DB error). Iron Law 5.
                    # Mirrors record_labor's posting_date gate.
                    effective_date = body.posting_date or _date_ro.today()
                    fp_ro = await conn.fetchrow(
                        """
                        SELECT status FROM fiscal_periods
                        WHERE tenant_id = $1
                          AND $2 BETWEEN start_date AND end_date
                        LIMIT 1
                        """,
                        ctx["tenant_id"],
                        effective_date,
                    )
                    if (not fp_ro) or str(fp_ro["status"]).upper() in (
                        "CLOSED",
                        "LOCKED",
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Tidak bisa lapor output: tanggal {effective_date} "
                                f"berada di periode yang sudah ditutup/dikunci atau "
                                f"tidak ada periode aktif. Pilih tanggal di periode "
                                f"yang masih OPEN."
                            ),
                        )

                    today_ro = effective_date
                    ym_ro = f"{today_ro.year % 100:02d}{today_ro.month:02d}"
                    # Self-healing canonical generator (V176): plain JV, effective date.
                    jnum_ro = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "JV",
                        today_ro,
                    )
                    fg_journal_id = _uuid_ro.uuid4()
                    total_cost_dec = Decimal(str(total_cost))
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id,
                            total_debit, total_credit, status, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'PRODUCTION_OUTPUT', $6, $7, $7, 'DRAFT', $8)
                        """,
                        fg_journal_id,
                        ctx["tenant_id"],
                        jnum_ro,
                        today_ro,
                        f"Penerimaan Produksi {order['order_number']} - {fg_product['nama_produk']}",
                        order_id,
                        total_cost_dec,
                        ctx["user_id"],
                    )
                    # Dr Persediaan FG
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo, item_id)
                        VALUES ($1, $2, 1, $3, $4, 0, $5, $6)
                        """,
                        _uuid_ro.uuid4(),
                        fg_journal_id,
                        fg_acct,
                        total_cost_dec,
                        f"Persediaan FG {order['order_number']}",
                        order["product_id"],
                    )
                    # Cr WIP
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        _uuid_ro.uuid4(),
                        fg_journal_id,
                        wip_acct,
                        total_cost_dec,
                        f"WIP {order['order_number']}",
                    )
                    # Law 20: POST
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        fg_journal_id,
                    )

                    # inventory_ledger PRODUCTION_OUTPUT (FG inbound)
                    fg_warehouse = (
                        body.warehouse_id
                        or order["warehouse_id"]
                        or fg_product["default_warehouse_id"]
                    )
                    current_bal_fg = await conn.fetchval(
                        "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id=$1 AND product_id=$2",
                        ctx["tenant_id"],
                        order["product_id"],
                    )
                    new_bal_fg = Decimal(str(current_bal_fg)) + Decimal(
                        str(body.good_quantity)
                    )
                    # WAC calc: ((old_value + new_value) / (old_qty + new_qty))
                    wac_row = await conn.fetchrow(
                        """
                        SELECT COALESCE(SUM(quantity_in * unit_cost), 0) AS total_value,
                               COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS total_qty
                        FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2
                        """,
                        ctx["tenant_id"],
                        order["product_id"],
                    )
                    old_val = Decimal(str(wac_row["total_value"] or 0))
                    old_qty = Decimal(str(wac_row["total_qty"] or 0))
                    qty_in = Decimal(str(body.good_quantity))
                    unit_cost_dec = Decimal(str(unit_cost))
                    if old_qty + qty_in > 0:
                        new_avg = (old_val + qty_in * unit_cost_dec) / (
                            old_qty + qty_in
                        )
                    else:
                        new_avg = unit_cost_dec
                    await conn.execute(
                        """
                        INSERT INTO inventory_ledger (
                            id, tenant_id, product_id, product_code, product_name,
                            movement_type, movement_date, source_type, source_id, source_number,
                            quantity_in, quantity_out, quantity_balance,
                            unit_cost, total_cost, average_cost,
                            warehouse_id, created_by, notes, journal_id
                        ) VALUES (
                            gen_random_uuid(), $1, $2, $3, $4,
                            'PRODUCTION_OUTPUT', $16, 'PRODUCTION_OUTPUT', $5, $6,
                            $7, 0, $8,
                            $9, $10, $11,
                            $12, $13, $14, $15
                        )
                        """,
                        ctx["tenant_id"],
                        order["product_id"],
                        fg_product["item_code"],
                        fg_product["nama_produk"],
                        order_id,
                        order["order_number"],
                        qty_in,
                        new_bal_fg,
                        unit_cost_dec,
                        total_cost_dec,
                        new_avg,
                        fg_warehouse,
                        ctx["user_id"],
                        f"Penerimaan produksi {order['order_number']}",
                        fg_journal_id,
                        today_ro,
                    )

                    # Link journal to completion + WO
                    await conn.execute(
                        "UPDATE production_completions SET journal_id = $1 WHERE id = $2",
                        fg_journal_id,
                        completion_id,
                    )
                    await conn.execute(
                        "UPDATE production_orders SET completion_journal_id = $1 WHERE id = $2",
                        fg_journal_id,
                        order_id,
                    )

                return {
                    "success": True,
                    "message": "Output recorded",
                    "data": {
                        "id": str(completion_id),
                        "unit_cost": unit_cost,
                        "total_cost": total_cost,
                        "journal_id": str(fg_journal_id) if fg_journal_id else None,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reporting output: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to report output")


# =============================================================================
# COST ANALYSIS
# =============================================================================
@router.get("/{order_id}/cost-analysis", response_model=CostAnalysisResponse)
async def get_cost_analysis(request: Request, order_id: UUID):
    """Get cost analysis for production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT po.*, p.nama_produk as product_name
                FROM production_orders po
                JOIN products p ON p.id = po.product_id
                WHERE po.tenant_id = $1 AND po.id = $2
                """,
                ctx["tenant_id"],
                order_id,
            )
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            analysis = []

            # Material
            mat_planned = order["planned_material_cost"]
            mat_actual = order["actual_material_cost"]
            mat_var = mat_actual - mat_planned
            analysis.append(
                {
                    "category": "material",
                    "planned": mat_planned,
                    "actual": mat_actual,
                    "variance": mat_var,
                    "variance_percent": round(
                        Decimal(mat_var / mat_planned * 100) if mat_planned else 0, 2
                    ),
                }
            )

            # Labor
            lab_planned = order["planned_labor_cost"]
            lab_actual = order["actual_labor_cost"]
            lab_var = lab_actual - lab_planned
            analysis.append(
                {
                    "category": "labor",
                    "planned": lab_planned,
                    "actual": lab_actual,
                    "variance": lab_var,
                    "variance_percent": round(
                        Decimal(lab_var / lab_planned * 100) if lab_planned else 0, 2
                    ),
                }
            )

            # Overhead
            oh_planned = order["planned_overhead_cost"]
            oh_actual = order["actual_overhead_cost"]
            oh_var = oh_actual - oh_planned
            analysis.append(
                {
                    "category": "overhead",
                    "planned": oh_planned,
                    "actual": oh_actual,
                    "variance": oh_var,
                    "variance_percent": round(
                        Decimal(oh_var / oh_planned * 100) if oh_planned else 0, 2
                    ),
                }
            )

            # Fetch subcontract records
            subcontracts = await conn.fetch(
                """
                SELECT ps.*, op.operation_name, v.name AS vendor_name,
                       b.invoice_number AS bill_number
                FROM production_subcontracts ps
                JOIN bom_operations op ON op.id = ps.bom_operation_id
                JOIN vendors v ON v.id = ps.vendor_id
                LEFT JOIN bills b ON b.id = ps.bill_id
                WHERE ps.production_order_id = $1 AND ps.tenant_id = $2
            """,
                order_id,
                ctx["tenant_id"],
            )

            sc_cost = Decimal(str(order["subcontract_cost"] or 0))
            total_planned = mat_planned + lab_planned + oh_planned
            total_actual = mat_actual + lab_actual + oh_actual + sc_cost
            unit_cost = (
                int(total_actual / Decimal(str(order["completed_quantity"])))
                if order["completed_quantity"]
                else 0
            )

            return {
                "success": True,
                "order_number": order["order_number"],
                "product_name": order["product_name"],
                "planned_quantity": order["planned_quantity"],
                "completed_quantity": order["completed_quantity"],
                "analysis": analysis,
                "total_planned": total_planned,
                "total_actual": total_actual,
                "total_variance": total_actual - total_planned,
                "unit_cost": unit_cost,
                "subcontract_cost": float(order["subcontract_cost"] or 0),
                "total_cost": float(order["total_cost"] or 0),
                "subcontracts": [
                    {
                        "id": str(s["id"]),
                        "operation": s["operation_name"],
                        "vendor": s["vendor_name"],
                        "quantity": float(s["quantity"]),
                        "unit_cost": float(s["unit_cost"]),
                        "total_cost": float(s["total_cost"]),
                        "bill_number": s["bill_number"],
                        "bill_status": s["bill_status"],
                        "status": s["status"],
                    }
                    for s in subcontracts
                ],
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cost analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cost analysis")


# =============================================================================
# QUERIES
# =============================================================================
@router.get("/active")
async def get_active_orders(request: Request):
    """Get in-progress production orders."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT po.*, p.nama_produk as product_name
                FROM production_orders po
                JOIN products p ON p.id = po.product_id
                WHERE po.tenant_id = $1 AND po.status IN ('released', 'in_progress')
                ORDER BY po.priority, po.planned_start_date
                """,
                ctx["tenant_id"],
            )

            items = [
                {
                    "id": str(row["id"]),
                    "order_number": row["order_number"],
                    "product_name": row["product_name"],
                    "planned_quantity": row["planned_quantity"],
                    "completed_quantity": row["completed_quantity"],
                    "status": row["status"],
                    "priority": row["priority"],
                }
                for row in rows
            ]

            return {"success": True, "items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting active orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get active orders")


@router.get("/schedule", response_model=ProductionScheduleResponse)
async def get_production_schedule(
    request: Request, start_date: date = Query(...), end_date: date = Query(...)
):
    """Get production schedule."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT po.*, p.nama_produk as product_name, wc.name as work_center_name
                FROM production_orders po
                JOIN products p ON p.id = po.product_id
                LEFT JOIN work_centers wc ON wc.id = po.work_center_id
                WHERE po.tenant_id = $1
                  AND po.status NOT IN ('completed', 'cancelled')
                  AND (
                      (po.planned_start_date BETWEEN $2 AND $3) OR
                      (po.planned_end_date BETWEEN $2 AND $3) OR
                      (po.planned_start_date <= $2 AND po.planned_end_date >= $3)
                  )
                ORDER BY po.planned_start_date, po.priority
                """,
                ctx["tenant_id"],
                start_date,
                end_date,
            )

            items = [
                {
                    "order_id": str(row["id"]),
                    "order_number": row["order_number"],
                    "product_name": row["product_name"],
                    "planned_quantity": row["planned_quantity"],
                    "planned_start": row["planned_start_date"],
                    "planned_end": row["planned_end_date"],
                    "work_center_name": row["work_center_name"],
                    "status": row["status"],
                    "priority": row["priority"],
                }
                for row in rows
            ]

            return {
                "success": True,
                "start_date": start_date,
                "end_date": end_date,
                "items": items,
                "total_orders": len(items),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting production schedule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get production schedule")


from ..services.role_resolver import AccountRoleUnmappedError  # noqa: E402 (reconcile OH-skip guard)

# =============================================================================
# MONTH-END MANUFACTURING RECONCILE (Fase G-12 / Deep-val 2.5 closeout)
# -----------------------------------------------------------------------------
# Policy = FULL ABSORPTION. At month end we drain the applied-cost clearing
# accounts (MFG_LABOR_APPLIED 2-10430 / MFG_OVERHEAD_APPLIED 2-10440), zero out
# the actual expense accounts (MFG_DIRECT_LABOR 5-20100 / MFG_ACTUAL_OVERHEAD
# 5-30300), and plug the difference to COGS_VARIANCE_PRODUCTION 5-90200.
#
# Goal: Beban Gaji (5-20100) and Actual OH (5-30300) net to ZERO for the period
# (fully absorbed into WIP via applied clearing + variance), leaving only the
# variance in 5-90200.
#
# Conventions copied verbatim from record_labor / complete_order variance flush:
#   - resolve_account_id_by_role(conn, tenant_id, AccountRole.*)
#   - pg_advisory_xact_lock(hashtext($1)) at top of tx
#   - JV sequence via journal_number_sequences ON CONFLICT bump
#   - journal_entries DRAFT -> POSTED (Law 20), conn.transaction() wrap
#   - Law 4 (Dr=Cr asserted), Law 25 (Decimal quantize 0.01), Law 27 (role-based)
#   - Void path reuses _reverse_journal (Law 2/26 single-reversal, reversal_of_id)
# =============================================================================

_RECON_SOURCE_TYPE = "PRODUCTION_RECONCILE"


@router.post("/month-end-reconcile", response_model=ProductionResponse)
async def month_end_reconcile(request: Request, body: dict):
    """Month-end manufacturing reconcile (full absorption).

    Body: {"period": "YYYY-MM"}

    Drains labor/OH applied clearing, zeroes actual labor/OH expense, plugs the
    difference to production variance (5-90200). One POSTED journal of
    source_type=PRODUCTION_RECONCILE per tenant+period (idempotent; re-runnable
    only after the reconcile journal is voided/reversed).
    """
    try:
        import uuid as _uuid_rc
        from decimal import Decimal as _D

        ctx = get_user_context(request)
        tenant_id = ctx["tenant_id"]
        user_id = ctx.get("user_id")

        period = (body or {}).get("period")
        if not period or not isinstance(period, str):
            raise HTTPException(
                status_code=400, detail="Body requires 'period' as 'YYYY-MM'"
            )

        dry_run = bool((body or {}).get("dry_run"))

        pool = await get_pool()
        await _ensure_production_role_preconditions(pool, ctx["tenant_id"])

        Q = _D("0.01")
        ZERO = _D("0")

        async with pool.acquire() as conn:
            async with conn.transaction():
                # --- Advisory lock (period-scoped) -------------------------
                # Dry-run is a pure read: no lock (no write to serialize).
                if not dry_run:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"MFG_RECONCILE:{tenant_id}:{period}",
                    )

                # --- Resolve fiscal period window --------------------------
                fp = await conn.fetchrow(
                    """
                    SELECT id, period_name, start_date, end_date, status
                    FROM fiscal_periods
                    WHERE tenant_id = $1 AND period_name = $2
                    """,
                    tenant_id,
                    period,
                )
                if not fp:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Fiscal period '{period}' not found for tenant",
                    )
                period_status = str(fp["status"]).upper()
                if period_status in ("CLOSED", "LOCKED") and not dry_run:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Period '{period}' is {fp['status']}; reconcile must "
                            f"run before the period is closed/locked."
                        ),
                    )
                p_start = fp["start_date"]
                p_end = fp["end_date"]

                # --- Idempotency guard -------------------------------------
                # A POSTED reconcile journal for this period blocks re-run. A
                # reversed/void reconcile is treated as 'not reconciled' so the
                # void path enables a clean re-run.
                existing = await conn.fetchrow(
                    """
                    SELECT id, journal_number
                    FROM journal_entries
                    WHERE tenant_id = $1
                      AND source_type = $2
                      AND status = 'POSTED'
                      AND reversed_by_id IS NULL
                      AND reversal_of_id IS NULL
                      AND is_effective_journal(id)
                      AND journal_date BETWEEN $3 AND $4
                    LIMIT 1
                    """,
                    tenant_id,
                    _RECON_SOURCE_TYPE,
                    p_start,
                    p_end,
                )
                already_reconciled = existing is not None
                if existing and not dry_run:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Period '{period}' already reconciled "
                            f"({existing['journal_number']}). Void it first to re-run."
                        ),
                    )

                # --- Resolve roles -> account_ids --------------------------
                labor_applied_id = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.MFG_LABOR_APPLIED
                )
                direct_labor_id = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.MFG_DIRECT_LABOR
                )
                oh_applied_id = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.MFG_OVERHEAD_APPLIED
                )
                variance_id = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.COGS_VARIANCE_PRODUCTION
                )

                # MFG_ACTUAL_OVERHEAD is a NEW role (parallel agent). If absent
                # from the catalog (ValueError) or unmapped for this tenant
                # (AccountRoleUnmappedError), skip the OH leg gracefully.
                actual_oh_id = None
                try:
                    actual_oh_id = await resolve_account_id_by_role(
                        conn, tenant_id, "MFG_ACTUAL_OVERHEAD"
                    )
                except (ValueError, AccountRoleUnmappedError):
                    actual_oh_id = None
                    logger.info(
                        "month_end_reconcile: MFG_ACTUAL_OVERHEAD unmapped — "
                        "skipping OH leg for tenant %s period %s",
                        tenant_id,
                        period,
                    )

                # --- Compute period figures (Decimal, effective-only) ------
                async def _signed_sum(
                    account_id, sign_debit: bool, extra_sql: str = "", *extra_args
                ):
                    # sign_debit=True  -> Σ(debit - credit)
                    # sign_debit=False -> Σ(credit - debit)
                    expr = (
                        "COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)"
                        if sign_debit
                        else "COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)"
                    )
                    val = await conn.fetchval(
                        f"""
                        SELECT {expr}
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE je.tenant_id = $1
                          AND jl.account_id = $2
                          AND je.journal_date BETWEEN $3 AND $4
                          AND is_effective_journal(je.id)
                          {extra_sql}
                        """,
                        tenant_id,
                        account_id,
                        p_start,
                        p_end,
                        *extra_args,
                    )
                    return _D(str(val or 0)).quantize(Q)

                # applied_labor = Σ(credit - debit) on 2-10430 (absorption only)
                # Scope to PRODUCTION_LABOR absorption journals so labeled
                # residual-disposition / adjustment journals posted into the
                # same clearing account during the window do NOT shrink the
                # drain; drain must equal exactly the period's absorption.
                applied_labor = await _signed_sum(
                    labor_applied_id,
                    sign_debit=False,
                    extra_sql="AND je.source_type = 'PRODUCTION_LABOR'",
                )
                # actual_labor = Σ(debit - credit) on 5-20100 from PAYROLL only,
                # EXCLUDE CLOSING (so a prior close's zero-out doesn't double count)
                actual_labor = await _signed_sum(
                    direct_labor_id,
                    sign_debit=True,
                    extra_sql="AND je.source_type = 'PAYROLL'",
                )

                applied_oh = ZERO
                actual_oh = ZERO
                if actual_oh_id is not None:
                    # Scope to PRODUCTION_OVERHEAD absorption journals only
                    # (same rationale as applied_labor above).
                    applied_oh = await _signed_sum(
                        oh_applied_id,
                        sign_debit=False,
                        extra_sql="AND je.source_type = 'PRODUCTION_OVERHEAD'",
                    )
                    actual_oh = await _signed_sum(
                        actual_oh_id,
                        sign_debit=True,
                        extra_sql="AND je.source_type <> 'CLOSING'",
                    )

                labor_active = (applied_labor != ZERO) or (actual_labor != ZERO)
                oh_active = (actual_oh_id is not None) and (
                    (applied_oh != ZERO) or (actual_oh != ZERO)
                )

                # --- DRY-RUN preview: pure read, no lock/insert/409 --------
                # Numbers come from the IDENTICAL computation above (anti-drift).
                # Additionally surface CURRENT effective clearing balances so the
                # FE can show "saldo clearing sekarang -> akan jadi 0".
                if dry_run:
                    clr_labor = await _signed_sum(labor_applied_id, sign_debit=False)
                    clr_oh = (
                        await _signed_sum(oh_applied_id, sign_debit=False)
                        if actual_oh_id is not None
                        else ZERO
                    )
                    var_labor_dr = (actual_labor - applied_labor).quantize(Q)
                    var_oh_dr = (
                        (actual_oh - applied_oh).quantize(Q)
                        if actual_oh_id is not None
                        else ZERO
                    )
                    return {
                        "success": True,
                        "message": "Month-end reconcile preview (dry-run)",
                        "data": {
                            "period": period,
                            "dry_run": True,
                            "period_status": period_status,
                            "already_reconciled": already_reconciled,
                            "journal_id": (
                                str(existing["id"]) if already_reconciled else None
                            ),
                            "journal_number": (
                                existing["journal_number"]
                                if already_reconciled
                                else None
                            ),
                            "labor": {
                                "applied": str(applied_labor),
                                "actual": str(actual_labor),
                                "variance": str(var_labor_dr),
                            },
                            "overhead": {
                                "mapped": actual_oh_id is not None,
                                "applied": str(applied_oh),
                                "actual": str(actual_oh),
                                "variance": str(var_oh_dr),
                            },
                            "clearing": {
                                "labor_2_10430": str(clr_labor),
                                "oh_2_10440": str(clr_oh),
                            },
                        },
                    }

                if not labor_active and not oh_active:
                    return {
                        "success": True,
                        "message": "Nothing to reconcile for this period",
                        "data": {
                            "period": period,
                            "labor": {
                                "applied": str(applied_labor),
                                "actual": str(actual_labor),
                                "variance": "0.00",
                            },
                            "overhead": {
                                "mapped": actual_oh_id is not None,
                                "applied": str(applied_oh),
                                "actual": str(actual_oh),
                                "variance": "0.00",
                            },
                            "journal_number": None,
                        },
                    }

                # --- Build the reconcile journal ---------------------------
                ym = f"{p_end.year % 100:02d}{p_end.month:02d}"
                # Self-healing canonical generator (V176): emits JV-RECON, bumps JV-RECON counter.
                jnum = await conn.fetchval(
                    "SELECT get_next_journal_number($1, $2, $3)",
                    tenant_id,
                    "JV-RECON",
                    p_end,
                )
                je_id = _uuid_rc.uuid4()

                lines = []  # (account_id, debit, credit, memo)

                var_labor = ZERO
                var_oh = ZERO

                # ---- LABOR leg ----------------------------------------------
                # Dr 2-10430 applied_labor (drain clearing; if positive)
                # Cr 5-20100 actual_labor  (zero out Beban Gaji; if positive)
                # plug var_labor = actual_labor - applied_labor to 5-90200
                #   var>0 -> Dr 5-90200 (under-applied/unfavorable)
                #   var<0 -> Cr 5-90200 (over-applied/favorable)
                if labor_active:
                    if applied_labor > ZERO:
                        lines.append(
                            (
                                labor_applied_id,
                                applied_labor,
                                ZERO,
                                f"Drain TKL Applied {period}",
                            )
                        )
                    elif applied_labor < ZERO:
                        # clearing had a debit balance — credit it back
                        lines.append(
                            (
                                labor_applied_id,
                                ZERO,
                                -applied_labor,
                                f"Drain TKL Applied (neg) {period}",
                            )
                        )
                    if actual_labor > ZERO:
                        lines.append(
                            (
                                direct_labor_id,
                                ZERO,
                                actual_labor,
                                f"Zero Beban Gaji {period}",
                            )
                        )
                    elif actual_labor < ZERO:
                        lines.append(
                            (
                                direct_labor_id,
                                -actual_labor,
                                ZERO,
                                f"Zero Beban Gaji (neg) {period}",
                            )
                        )
                    var_labor = (actual_labor - applied_labor).quantize(Q)
                    if var_labor > ZERO:
                        lines.append(
                            (
                                variance_id,
                                var_labor,
                                ZERO,
                                f"Labor variance (unfavorable) {period}",
                            )
                        )
                    elif var_labor < ZERO:
                        lines.append(
                            (
                                variance_id,
                                ZERO,
                                -var_labor,
                                f"Labor variance (favorable) {period}",
                            )
                        )

                # ---- OH leg (only if MFG_ACTUAL_OVERHEAD mapped) ------------
                if oh_active:
                    if applied_oh > ZERO:
                        lines.append(
                            (
                                oh_applied_id,
                                applied_oh,
                                ZERO,
                                f"Drain Overhead Applied {period}",
                            )
                        )
                    elif applied_oh < ZERO:
                        lines.append(
                            (
                                oh_applied_id,
                                ZERO,
                                -applied_oh,
                                f"Drain Overhead Applied (neg) {period}",
                            )
                        )
                    if actual_oh > ZERO:
                        lines.append(
                            (
                                actual_oh_id,
                                ZERO,
                                actual_oh,
                                f"Zero Actual Overhead {period}",
                            )
                        )
                    elif actual_oh < ZERO:
                        lines.append(
                            (
                                actual_oh_id,
                                -actual_oh,
                                ZERO,
                                f"Zero Actual Overhead (neg) {period}",
                            )
                        )
                    var_oh = (actual_oh - applied_oh).quantize(Q)
                    if var_oh > ZERO:
                        lines.append(
                            (
                                variance_id,
                                var_oh,
                                ZERO,
                                f"Overhead variance (unfavorable) {period}",
                            )
                        )
                    elif var_oh < ZERO:
                        lines.append(
                            (
                                variance_id,
                                ZERO,
                                -var_oh,
                                f"Overhead variance (favorable) {period}",
                            )
                        )

                # --- Law 4: assert Dr == Cr before POSTED ------------------
                total_debit = sum((ln[1] for ln in lines), ZERO).quantize(Q)
                total_credit = sum((ln[2] for ln in lines), ZERO).quantize(Q)
                if total_debit != total_credit:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            f"Reconcile journal unbalanced (Law 4): "
                            f"Dr {total_debit} != Cr {total_credit}"
                        ),
                    )
                if not lines:
                    return {
                        "success": True,
                        "message": "Nothing to reconcile for this period",
                        "data": {"period": period, "journal_number": None},
                    }

                # --- Insert DRAFT journal + lines --------------------------
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        total_debit, total_credit, status, period_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'DRAFT', $10, $11)
                    """,
                    je_id,
                    tenant_id,
                    jnum,
                    p_end,
                    f"Month-end manufacturing reconcile {period} (full absorption)",
                    _RECON_SOURCE_TYPE,
                    je_id,  # self-referencing source_id (period-level, no WO)
                    total_debit,
                    total_credit,
                    fp["id"],
                    user_id,
                )
                for idx, (acc_id, dr, cr, memo) in enumerate(lines, start=1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        _uuid_rc.uuid4(),
                        je_id,
                        idx,
                        acc_id,
                        dr,
                        cr,
                        memo,
                    )

                # --- Law 20: DRAFT -> POSTED -------------------------------
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    je_id,
                )

                logger.info(
                    "month_end_reconcile %s period=%s labor(app=%s act=%s var=%s) "
                    "oh(mapped=%s app=%s act=%s var=%s)",
                    jnum,
                    period,
                    applied_labor,
                    actual_labor,
                    var_labor,
                    actual_oh_id is not None,
                    applied_oh,
                    actual_oh,
                    var_oh,
                )

                return {
                    "success": True,
                    "message": f"Month-end reconcile posted ({jnum})",
                    "data": {
                        "period": period,
                        "journal_id": str(je_id),
                        "journal_number": jnum,
                        "labor": {
                            "applied": str(applied_labor),
                            "actual": str(actual_labor),
                            "variance": str(var_labor),
                        },
                        "overhead": {
                            "mapped": actual_oh_id is not None,
                            "applied": str(applied_oh),
                            "actual": str(actual_oh),
                            "variance": str(var_oh),
                        },
                        "total_debit": str(total_debit),
                        "total_credit": str(total_credit),
                    },
                }

    except HTTPException:
        raise
    except AccountRoleUnmappedError as e:
        logger.error("month_end_reconcile role unmapped: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error in month_end_reconcile: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reconcile month-end")


@router.post(
    "/month-end-reconcile/{journal_id}/void", response_model=ProductionResponse
)
async def void_month_end_reconcile(request: Request, journal_id: UUID):
    """Void (reverse) a month-end reconcile journal (Law 2 + Law 26).

    Creates a reversal journal linked via reversal_of_id; the period then reads
    as 'not reconciled' (idempotency guard skips reversed reconciles) so it can
    be re-run after new payroll/OH postings land.
    """
    try:
        ctx = get_user_context(request)
        tenant_id = ctx["tenant_id"]
        user_id = ctx.get("user_id")

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"MFG_RECONCILE_VOID:{tenant_id}:{journal_id}",
                )

                je = await conn.fetchrow(
                    """
                    SELECT id, journal_number, source_type, status, reversed_by_id,
                           journal_date
                    FROM journal_entries
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    journal_id,
                    tenant_id,
                )
                if not je:
                    raise HTTPException(
                        status_code=404, detail="Reconcile journal not found"
                    )
                if je["source_type"] != _RECON_SOURCE_TYPE:
                    raise HTTPException(
                        status_code=400,
                        detail="Journal is not a month-end reconcile",
                    )
                if je["status"] != "POSTED":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot void: journal status is {je['status']}",
                    )
                if je["reversed_by_id"]:
                    raise HTTPException(
                        status_code=409, detail="Reconcile already reversed"
                    )

                # Guard: cannot reverse into a closed/locked period
                fp = await conn.fetchrow(
                    """
                    SELECT status FROM fiscal_periods
                    WHERE tenant_id = $1
                      AND $2 BETWEEN start_date AND end_date
                    LIMIT 1
                    """,
                    tenant_id,
                    je["journal_date"],
                )
                if fp and str(fp["status"]).upper() in ("CLOSED", "LOCKED"):
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void: target period is closed/locked",
                    )

                rev_id = await _reverse_journal(
                    conn,
                    tenant_id,
                    user_id,
                    journal_id,
                    "Void month-end manufacturing reconcile",
                )
                if rev_id is None:
                    raise HTTPException(
                        status_code=409,
                        detail="Reversal failed (already reversed or not posted)",
                    )

                logger.info(
                    "void_month_end_reconcile %s reversed by %s",
                    je["journal_number"],
                    rev_id,
                )
                return {
                    "success": True,
                    "message": f"Reconcile {je['journal_number']} reversed",
                    "data": {
                        "original_journal_id": str(journal_id),
                        "reversal_journal_id": str(rev_id),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error voiding month_end_reconcile: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void reconcile")
