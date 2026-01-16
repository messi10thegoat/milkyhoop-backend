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
    CapacityResponse,
    ProductionResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

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


# =============================================================================
# HEALTH CHECK
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "production"}


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
                conditions.append(f"(po.order_number ILIKE ${param_idx} OR p.nama_produk ILIKE ${param_idx})")
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
                "priority": "po.priority"
            }[sort_by]

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM production_orders po JOIN products p ON p.id = po.product_id WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT po.*, p.nama_produk as product_name, p.sku as product_sku
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
                    completion_pct = round(float(row["completed_quantity"]) / float(row["planned_quantity"]) * 100, 2)

                items.append({
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
                })

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
                    ctx["tenant_id"], body.bom_id
                )
                if not bom:
                    raise HTTPException(status_code=400, detail="Active BOM not found")

                # Generate order number
                order_number = await conn.fetchval(
                    "SELECT generate_production_order_number($1)",
                    ctx["tenant_id"]
                )

                # Calculate planned costs based on BOM
                multiplier = float(body.planned_quantity) / float(bom["output_quantity"])
                planned_material = int(bom["standard_cost"] * multiplier)
                planned_labor = int(bom["labor_cost"] * multiplier)
                planned_overhead = int(bom["overhead_cost"] * multiplier)

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
                    ctx["tenant_id"], order_number, body.product_id, body.bom_id,
                    body.planned_quantity, body.unit, body.planned_start_date,
                    body.planned_end_date, body.work_center_id, body.warehouse_id,
                    body.sales_order_id, body.customer_id,
                    planned_material, planned_labor, planned_overhead,
                    body.priority, body.notes, ctx["user_id"]
                )

                # Create planned materials from BOM components
                components = await conn.fetch(
                    """
                    SELECT bc.*, p.nama_produk as product_name
                    FROM bom_components bc
                    JOIN products p ON p.id = bc.component_product_id
                    WHERE bc.bom_id = $1
                    """,
                    body.bom_id
                )

                for comp in components:
                    planned_qty = float(comp["quantity"]) * multiplier * (1 + float(comp["wastage_percent"] or 0) / 100)
                    planned_cost = int(planned_qty * comp["unit_cost"])

                    await conn.execute(
                        """
                        INSERT INTO production_order_materials (
                            production_order_id, product_id, planned_quantity,
                            unit, planned_cost
                        ) VALUES ($1, $2, $3, $4, $5)
                        """,
                        order_id, comp["component_product_id"],
                        Decimal(str(round(planned_qty, 4))), comp["unit"], planned_cost
                    )

                return {
                    "success": True,
                    "message": "Production order created",
                    "data": {"id": str(order_id), "order_number": order_number}
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
                       bom.bom_code, wc.name as work_center_name, w.name as warehouse_name
                FROM production_orders po
                JOIN products p ON p.id = po.product_id
                JOIN bill_of_materials bom ON bom.id = po.bom_id
                LEFT JOIN work_centers wc ON wc.id = po.work_center_id
                LEFT JOIN warehouses w ON w.id = po.warehouse_id
                WHERE po.tenant_id = $1 AND po.id = $2
                """,
                ctx["tenant_id"], order_id
            )
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

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
                order_id
            )

            # Get labor
            labor = await conn.fetch(
                """
                SELECT * FROM production_order_labor
                WHERE production_order_id = $1
                ORDER BY created_at
                """,
                order_id
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
                order_id
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
                    "work_center_id": str(order["work_center_id"]) if order["work_center_id"] else None,
                    "work_center_name": order["work_center_name"],
                    "warehouse_id": str(order["warehouse_id"]) if order["warehouse_id"] else None,
                    "warehouse_name": order["warehouse_name"],
                    "sales_order_id": str(order["sales_order_id"]) if order["sales_order_id"] else None,
                    "customer_id": str(order["customer_id"]) if order["customer_id"] else None,
                    "planned_material_cost": order["planned_material_cost"],
                    "planned_labor_cost": order["planned_labor_cost"],
                    "planned_overhead_cost": order["planned_overhead_cost"],
                    "actual_material_cost": order["actual_material_cost"],
                    "actual_labor_cost": order["actual_labor_cost"],
                    "actual_overhead_cost": order["actual_overhead_cost"],
                    "variance_amount": order["variance_amount"],
                    "status": order["status"],
                    "priority": order["priority"],
                    "material_issue_journal_id": str(order["material_issue_journal_id"]) if order["material_issue_journal_id"] else None,
                    "labor_journal_id": str(order["labor_journal_id"]) if order["labor_journal_id"] else None,
                    "completion_journal_id": str(order["completion_journal_id"]) if order["completion_journal_id"] else None,
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
                            "warehouse_id": str(m["warehouse_id"]) if m["warehouse_id"] else None,
                        }
                        for m in materials
                    ],
                    "labor": [
                        {
                            "id": str(l["id"]),
                            "operation_id": str(l["operation_id"]) if l["operation_id"] else None,
                            "operation_name": l["operation_name"],
                            "planned_hours": l["planned_hours"],
                            "planned_cost": l["planned_cost"],
                            "actual_hours": l["actual_hours"],
                            "actual_cost": l["actual_cost"],
                            "worker_id": str(l["worker_id"]) if l["worker_id"] else None,
                            "worker_name": l["worker_name"],
                            "start_time": l["start_time"],
                            "end_time": l["end_time"],
                            "hourly_rate": l["hourly_rate"],
                            "notes": l["notes"],
                            "created_at": l["created_at"],
                        }
                        for l in labor
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
                            "warehouse_id": str(c["warehouse_id"]) if c["warehouse_id"] else None,
                            "batch_id": str(c["batch_id"]) if c["batch_id"] else None,
                            "batch_number": c["batch_number"],
                            "journal_id": str(c["journal_id"]) if c["journal_id"] else None,
                            "completed_by": str(c["completed_by"]) if c["completed_by"] else None,
                            "created_at": c["created_at"],
                        }
                        for c in completions
                    ],
                    "created_at": order["created_at"],
                    "updated_at": order["updated_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting production order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get production order")


@router.patch("/{order_id}", response_model=ProductionResponse)
async def update_production_order(request: Request, order_id: UUID, body: UpdateProductionOrderRequest):
    """Update production order (draft/planned only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                "SELECT status FROM production_orders WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], order_id
            )
            if not order:
                raise HTTPException(status_code=404, detail="Production order not found")

            if order["status"] not in ("draft", "planned"):
                raise HTTPException(status_code=400, detail="Can only update draft or planned orders")

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
                *params
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
                ctx["tenant_id"], order_id
            )
            if result == "DELETE 0":
                raise HTTPException(status_code=400, detail="Order not found or not in draft status")

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
    """Release order to production."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE production_orders
                SET status = 'released', updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status IN ('draft', 'planned')
                """,
                ctx["tenant_id"], order_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Order not found or cannot be released")

            return {"success": True, "message": "Production order released"}

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
                ctx["tenant_id"], order_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Order not found or not released")

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
            order = await conn.fetchrow(
                """
                SELECT * FROM production_orders
                WHERE tenant_id = $1 AND id = $2
                """,
                ctx["tenant_id"], order_id
            )
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            if order["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Order not in progress")

            # Calculate variance
            actual_total = order["actual_material_cost"] + order["actual_labor_cost"] + order["actual_overhead_cost"]
            planned_total = order["planned_material_cost"] + order["planned_labor_cost"] + order["planned_overhead_cost"]
            variance = actual_total - planned_total

            await conn.execute(
                """
                UPDATE production_orders
                SET status = 'completed', actual_end_date = CURRENT_DATE,
                    variance_amount = $3, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                """,
                ctx["tenant_id"], order_id, variance
            )

            return {
                "success": True,
                "message": "Production order completed",
                "data": {"variance_amount": variance}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to complete order")


@router.post("/{order_id}/cancel", response_model=ProductionResponse)
async def cancel_order(request: Request, order_id: UUID):
    """Cancel production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE production_orders
                SET status = 'cancelled', updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status IN ('draft', 'planned', 'released')
                """,
                ctx["tenant_id"], order_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Order not found or cannot be cancelled")

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
async def issue_materials(request: Request, order_id: UUID, materials: List[ProductionMaterialInput]):
    """Issue materials to production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM production_orders WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"], order_id
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                if order["status"] not in ("released", "in_progress"):
                    raise HTTPException(status_code=400, detail="Order must be released or in progress")

                total_issued_cost = 0

                for mat in materials:
                    # Get planned material
                    planned = await conn.fetchrow(
                        """
                        SELECT * FROM production_order_materials
                        WHERE production_order_id = $1 AND product_id = $2
                        """,
                        order_id, mat.product_id
                    )

                    # Get current cost for product
                    product = await conn.fetchrow(
                        "SELECT purchase_price FROM products WHERE id = $1",
                        mat.product_id
                    )
                    unit_cost = product["purchase_price"] if product else 0
                    issue_cost = int(float(mat.quantity) * unit_cost)

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
                            order_id, mat.product_id, mat.quantity, issue_cost,
                            ctx["user_id"], mat.warehouse_id, mat.batch_id
                        )
                    else:
                        # Add unplanned material
                        await conn.execute(
                            """
                            INSERT INTO production_order_materials (
                                production_order_id, product_id, planned_quantity, unit,
                                issued_quantity, actual_cost, issued_date, issued_by, warehouse_id, batch_id
                            ) VALUES ($1, $2, 0, $3, $4, $5, CURRENT_DATE, $6, $7, $8)
                            """,
                            order_id, mat.product_id, mat.unit, mat.quantity, issue_cost,
                            ctx["user_id"], mat.warehouse_id, mat.batch_id
                        )

                    total_issued_cost += issue_cost

                # Update order actual material cost
                await conn.execute(
                    """
                    UPDATE production_orders
                    SET actual_material_cost = actual_material_cost + $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    order_id, total_issued_cost
                )

                # TODO: Create journal entry Dr. WIP / Cr. Inventory

                return {
                    "success": True,
                    "message": f"Materials issued, total cost: {total_issued_cost}"
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
    """Record labor for production order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                "SELECT * FROM production_orders WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], order_id
            )
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            if order["status"] not in ("released", "in_progress"):
                raise HTTPException(status_code=400, detail="Order must be released or in progress")

            labor_cost = int(float(body.actual_hours) * body.hourly_rate)

            labor_id = await conn.fetchval(
                """
                INSERT INTO production_order_labor (
                    production_order_id, operation_id, operation_name,
                    actual_hours, actual_cost, worker_id, worker_name,
                    start_time, end_time, hourly_rate, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                order_id, body.operation_id, body.operation_name,
                body.actual_hours, labor_cost, body.worker_id, body.worker_name,
                body.start_time, body.end_time, body.hourly_rate, body.notes
            )

            # Update order actual labor cost
            await conn.execute(
                """
                UPDATE production_orders
                SET actual_labor_cost = actual_labor_cost + $2, updated_at = NOW()
                WHERE id = $1
                """,
                order_id, labor_cost
            )

            return {
                "success": True,
                "message": "Labor recorded",
                "data": {"id": str(labor_id), "cost": labor_cost}
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
async def report_output(request: Request, order_id: UUID, body: ProductionCompletionInput):
    """Report production output."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    "SELECT * FROM production_orders WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"], order_id
                )
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")

                if order["status"] not in ("released", "in_progress"):
                    raise HTTPException(status_code=400, detail="Order must be released or in progress")

                # Calculate unit cost
                total_actual = order["actual_material_cost"] + order["actual_labor_cost"] + order["actual_overhead_cost"]
                total_qty = float(order["completed_quantity"]) + float(body.good_quantity)
                unit_cost = int(total_actual / total_qty) if total_qty > 0 else 0
                total_cost = int(unit_cost * float(body.good_quantity))

                # Record completion
                completion_id = await conn.fetchval(
                    """
                    INSERT INTO production_completions (
                        production_order_id, completion_date, good_quantity,
                        scrap_quantity, quality_status, inspection_notes,
                        unit_cost, total_cost, warehouse_id, batch_id, completed_by
                    ) VALUES ($1, CURRENT_DATE, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    order_id, body.good_quantity, body.scrap_quantity,
                    body.quality_status, body.inspection_notes,
                    unit_cost, total_cost, body.warehouse_id, body.batch_id, ctx["user_id"]
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
                    order_id, body.good_quantity, body.scrap_quantity
                )

                # TODO: Create journal entry Dr. Finished Goods / Cr. WIP

                return {
                    "success": True,
                    "message": "Output recorded",
                    "data": {
                        "id": str(completion_id),
                        "unit_cost": unit_cost,
                        "total_cost": total_cost
                    }
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
                ctx["tenant_id"], order_id
            )
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            analysis = []

            # Material
            mat_planned = order["planned_material_cost"]
            mat_actual = order["actual_material_cost"]
            mat_var = mat_actual - mat_planned
            analysis.append({
                "category": "material",
                "planned": mat_planned,
                "actual": mat_actual,
                "variance": mat_var,
                "variance_percent": round(Decimal(mat_var / mat_planned * 100) if mat_planned else 0, 2)
            })

            # Labor
            lab_planned = order["planned_labor_cost"]
            lab_actual = order["actual_labor_cost"]
            lab_var = lab_actual - lab_planned
            analysis.append({
                "category": "labor",
                "planned": lab_planned,
                "actual": lab_actual,
                "variance": lab_var,
                "variance_percent": round(Decimal(lab_var / lab_planned * 100) if lab_planned else 0, 2)
            })

            # Overhead
            oh_planned = order["planned_overhead_cost"]
            oh_actual = order["actual_overhead_cost"]
            oh_var = oh_actual - oh_planned
            analysis.append({
                "category": "overhead",
                "planned": oh_planned,
                "actual": oh_actual,
                "variance": oh_var,
                "variance_percent": round(Decimal(oh_var / oh_planned * 100) if oh_planned else 0, 2)
            })

            total_planned = mat_planned + lab_planned + oh_planned
            total_actual = mat_actual + lab_actual + oh_actual
            unit_cost = int(total_actual / float(order["completed_quantity"])) if order["completed_quantity"] else 0

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
                "unit_cost": unit_cost
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
                ctx["tenant_id"]
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
    request: Request,
    start_date: date = Query(...),
    end_date: date = Query(...)
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
                ctx["tenant_id"], start_date, end_date
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
                "total_orders": len(items)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting production schedule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get production schedule")
