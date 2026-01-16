"""
BOM Router - Bill of Materials Management

Manages work centers, BOMs, components, and operations for manufacturing.

NOTE: BOM is master data - no journal entries.
Journal entries occur during Production Order execution.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
from decimal import Decimal
import logging
import asyncpg

from ..schemas.bom import (
    CreateWorkCenterRequest,
    UpdateWorkCenterRequest,
    WorkCenterListResponse,
    WorkCenterDetailResponse,
    CreateBOMRequest,
    UpdateBOMRequest,
    BOMListResponse,
    BOMDetailResponse,
    BOMComponentInput,
    BOMOperationInput,
    CostBreakdownResponse,
    MaterialsRequiredResponse,
    BOMExplosionResponse,
    WhereUsedResponse,
    BOMResponse,
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
    return {"status": "ok", "service": "bom"}


# =============================================================================
# WORK CENTERS
# =============================================================================
@router.get("/work-centers", response_model=WorkCenterListResponse)
async def list_work_centers(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    sort_by: Literal["name", "code", "created_at"] = Query("name"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
):
    """List work centers."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["wc.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(f"(wc.name ILIKE ${param_idx} OR wc.code ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            if is_active is not None:
                conditions.append(f"wc.is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            sort_column = {"name": "wc.name", "code": "wc.code", "created_at": "wc.created_at"}[sort_by]

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM work_centers wc WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT wc.*, w.name as warehouse_name
                FROM work_centers wc
                LEFT JOIN warehouses w ON w.id = wc.warehouse_id
                WHERE {where_clause}
                ORDER BY {sort_column} {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "warehouse_name": row["warehouse_name"],
                    "capacity_per_hour": row["capacity_per_hour"],
                    "labor_rate_per_hour": row["labor_rate_per_hour"],
                    "is_active": row["is_active"],
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing work centers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list work centers")


@router.post("/work-centers", response_model=BOMResponse, status_code=201)
async def create_work_center(request: Request, body: CreateWorkCenterRequest):
    """Create work center."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM work_centers WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], body.code
            )
            if exists:
                raise HTTPException(status_code=400, detail=f"Work center code '{body.code}' already exists")

            wc_id = await conn.fetchval(
                """
                INSERT INTO work_centers (
                    tenant_id, code, name, description, warehouse_id,
                    capacity_per_hour, hours_per_day, labor_rate_per_hour,
                    overhead_rate_per_hour, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                ctx["tenant_id"], body.code, body.name, body.description,
                body.warehouse_id, body.capacity_per_hour, body.hours_per_day,
                body.labor_rate_per_hour, body.overhead_rate_per_hour, ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Work center created",
                "data": {"id": str(wc_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating work center: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create work center")


@router.get("/work-centers/{work_center_id}", response_model=WorkCenterDetailResponse)
async def get_work_center(request: Request, work_center_id: UUID):
    """Get work center detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT wc.*, w.name as warehouse_name
                FROM work_centers wc
                LEFT JOIN warehouses w ON w.id = wc.warehouse_id
                WHERE wc.tenant_id = $1 AND wc.id = $2
                """,
                ctx["tenant_id"], work_center_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Work center not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "description": row["description"],
                    "warehouse_id": str(row["warehouse_id"]) if row["warehouse_id"] else None,
                    "warehouse_name": row["warehouse_name"],
                    "capacity_per_hour": row["capacity_per_hour"],
                    "hours_per_day": row["hours_per_day"],
                    "labor_rate_per_hour": row["labor_rate_per_hour"],
                    "overhead_rate_per_hour": row["overhead_rate_per_hour"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting work center: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get work center")


@router.patch("/work-centers/{work_center_id}", response_model=BOMResponse)
async def update_work_center(request: Request, work_center_id: UUID, body: UpdateWorkCenterRequest):
    """Update work center."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM work_centers WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], work_center_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Work center not found")

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
            params.extend([ctx["tenant_id"], work_center_id])

            await conn.execute(
                f"UPDATE work_centers SET {', '.join(updates)} WHERE tenant_id = ${param_idx} AND id = ${param_idx + 1}",
                *params
            )

            return {"success": True, "message": "Work center updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating work center: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update work center")


@router.delete("/work-centers/{work_center_id}", response_model=BOMResponse)
async def deactivate_work_center(request: Request, work_center_id: UUID):
    """Deactivate work center."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE work_centers SET is_active = false, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                """,
                ctx["tenant_id"], work_center_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Work center not found")

            return {"success": True, "message": "Work center deactivated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deactivating work center: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to deactivate work center")


# =============================================================================
# BILL OF MATERIALS
# =============================================================================
@router.get("", response_model=BOMListResponse)
async def list_boms(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    product_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    is_current: Optional[bool] = Query(None),
    sort_by: Literal["bom_code", "bom_name", "created_at"] = Query("bom_code"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
):
    """List BOMs."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["bom.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(f"(bom.bom_code ILIKE ${param_idx} OR bom.bom_name ILIKE ${param_idx} OR p.nama_produk ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            if product_id:
                conditions.append(f"bom.product_id = ${param_idx}")
                params.append(product_id)
                param_idx += 1

            if status:
                conditions.append(f"bom.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if is_current is not None:
                conditions.append(f"bom.is_current = ${param_idx}")
                params.append(is_current)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            sort_column = {"bom_code": "bom.bom_code", "bom_name": "bom.bom_name", "created_at": "bom.created_at"}[sort_by]

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bill_of_materials bom JOIN products p ON p.id = bom.product_id WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT bom.*, p.nama_produk as product_name, p.sku as product_sku,
                       (SELECT COUNT(*) FROM bom_components WHERE bom_id = bom.id) as component_count
                FROM bill_of_materials bom
                JOIN products p ON p.id = bom.product_id
                WHERE {where_clause}
                ORDER BY {sort_column} {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "bom_code": row["bom_code"],
                    "bom_name": row["bom_name"],
                    "product_id": str(row["product_id"]),
                    "product_name": row["product_name"],
                    "product_sku": row["product_sku"],
                    "version": row["version"],
                    "is_current": row["is_current"],
                    "output_quantity": row["output_quantity"],
                    "total_cost": row["total_cost"],
                    "component_count": row["component_count"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing BOMs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list BOMs")


@router.post("", response_model=BOMResponse, status_code=201)
async def create_bom(request: Request, body: CreateBOMRequest):
    """Create BOM with components and operations."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check duplicate
                exists = await conn.fetchval(
                    "SELECT 1 FROM bill_of_materials WHERE tenant_id = $1 AND bom_code = $2",
                    ctx["tenant_id"], body.bom_code
                )
                if exists:
                    raise HTTPException(status_code=400, detail=f"BOM code '{body.bom_code}' already exists")

                # Create BOM
                bom_id = await conn.fetchval(
                    """
                    INSERT INTO bill_of_materials (
                        tenant_id, product_id, bom_code, bom_name, description,
                        output_quantity, output_unit, estimated_time_minutes,
                        work_center_id, effective_date, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    ctx["tenant_id"], body.product_id, body.bom_code, body.bom_name,
                    body.description, body.output_quantity, body.output_unit,
                    body.estimated_time_minutes, body.work_center_id,
                    body.effective_date, ctx["user_id"]
                )

                # Create operations first (components may reference them)
                operation_ids = {}
                for op in body.operations:
                    op_id = await conn.fetchval(
                        """
                        INSERT INTO bom_operations (
                            bom_id, operation_number, operation_name, description,
                            work_center_id, setup_time_minutes, run_time_minutes,
                            labor_rate_per_hour, overhead_rate_per_hour, instructions
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        RETURNING id
                        """,
                        bom_id, op.operation_number, op.operation_name, op.description,
                        op.work_center_id, op.setup_time_minutes, op.run_time_minutes,
                        op.labor_rate_per_hour, op.overhead_rate_per_hour, op.instructions
                    )
                    operation_ids[op.operation_number] = op_id

                # Create components
                for comp in body.components:
                    await conn.execute(
                        """
                        INSERT INTO bom_components (
                            bom_id, component_product_id, quantity, unit,
                            wastage_percent, sequence_order, operation_id,
                            unit_cost, notes
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        bom_id, comp.component_product_id, comp.quantity, comp.unit,
                        comp.wastage_percent, comp.sequence_order, comp.operation_id,
                        comp.unit_cost, comp.notes
                    )

                # Calculate cost
                await conn.fetchval("SELECT calculate_bom_cost($1)", bom_id)

                return {
                    "success": True,
                    "message": "BOM created successfully",
                    "data": {"id": str(bom_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create BOM")


@router.get("/{bom_id}", response_model=BOMDetailResponse)
async def get_bom(request: Request, bom_id: UUID):
    """Get BOM detail with components and operations."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get BOM
            bom = await conn.fetchrow(
                """
                SELECT bom.*, p.nama_produk as product_name, p.sku as product_sku,
                       wc.name as work_center_name
                FROM bill_of_materials bom
                JOIN products p ON p.id = bom.product_id
                LEFT JOIN work_centers wc ON wc.id = bom.work_center_id
                WHERE bom.tenant_id = $1 AND bom.id = $2
                """,
                ctx["tenant_id"], bom_id
            )
            if not bom:
                raise HTTPException(status_code=404, detail="BOM not found")

            # Get operations
            operations = await conn.fetch(
                """
                SELECT bo.*, wc.name as work_center_name
                FROM bom_operations bo
                LEFT JOIN work_centers wc ON wc.id = bo.work_center_id
                WHERE bo.bom_id = $1
                ORDER BY bo.operation_number
                """,
                bom_id
            )

            # Get components
            components = await conn.fetch(
                """
                SELECT bc.*, p.nama_produk as product_name, p.sku as product_sku,
                       bo.operation_name
                FROM bom_components bc
                JOIN products p ON p.id = bc.component_product_id
                LEFT JOIN bom_operations bo ON bo.id = bc.operation_id
                WHERE bc.bom_id = $1
                ORDER BY bc.sequence_order, p.nama_produk
                """,
                bom_id
            )

            return {
                "success": True,
                "data": {
                    "id": str(bom["id"]),
                    "bom_code": bom["bom_code"],
                    "bom_name": bom["bom_name"],
                    "description": bom["description"],
                    "product_id": str(bom["product_id"]),
                    "product_name": bom["product_name"],
                    "product_sku": bom["product_sku"],
                    "version": bom["version"],
                    "is_current": bom["is_current"],
                    "effective_date": bom["effective_date"],
                    "obsolete_date": bom["obsolete_date"],
                    "output_quantity": bom["output_quantity"],
                    "output_unit": bom["output_unit"],
                    "standard_cost": bom["standard_cost"],
                    "labor_cost": bom["labor_cost"],
                    "overhead_cost": bom["overhead_cost"],
                    "total_cost": bom["total_cost"],
                    "estimated_time_minutes": bom["estimated_time_minutes"],
                    "work_center_id": str(bom["work_center_id"]) if bom["work_center_id"] else None,
                    "work_center_name": bom["work_center_name"],
                    "status": bom["status"],
                    "components": [
                        {
                            "id": str(c["id"]),
                            "component_product_id": str(c["component_product_id"]),
                            "component_product_name": c["product_name"],
                            "component_product_sku": c["product_sku"],
                            "quantity": c["quantity"],
                            "unit": c["unit"],
                            "wastage_percent": c["wastage_percent"],
                            "operation_id": str(c["operation_id"]) if c["operation_id"] else None,
                            "operation_name": c["operation_name"],
                            "unit_cost": c["unit_cost"],
                            "extended_cost": c["extended_cost"],
                            "notes": c["notes"],
                            "sequence_order": c["sequence_order"],
                            "is_substitute": c["is_substitute"],
                            "substitute_for_id": str(c["substitute_for_id"]) if c["substitute_for_id"] else None,
                        }
                        for c in components
                    ],
                    "operations": [
                        {
                            "id": str(o["id"]),
                            "operation_number": o["operation_number"],
                            "operation_name": o["operation_name"],
                            "description": o["description"],
                            "work_center_id": str(o["work_center_id"]) if o["work_center_id"] else None,
                            "work_center_name": o["work_center_name"],
                            "setup_time_minutes": o["setup_time_minutes"],
                            "run_time_minutes": o["run_time_minutes"],
                            "labor_rate_per_hour": o["labor_rate_per_hour"],
                            "overhead_rate_per_hour": o["overhead_rate_per_hour"],
                            "instructions": o["instructions"],
                        }
                        for o in operations
                    ],
                    "created_at": bom["created_at"],
                    "updated_at": bom["updated_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get BOM")


@router.patch("/{bom_id}", response_model=BOMResponse)
async def update_bom(request: Request, bom_id: UUID, body: UpdateBOMRequest):
    """Update BOM (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bom = await conn.fetchrow(
                "SELECT status FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], bom_id
            )
            if not bom:
                raise HTTPException(status_code=404, detail="BOM not found")

            if bom["status"] != "draft":
                raise HTTPException(status_code=400, detail="Can only update draft BOMs")

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
            params.extend([ctx["tenant_id"], bom_id])

            await conn.execute(
                f"UPDATE bill_of_materials SET {', '.join(updates)} WHERE tenant_id = ${param_idx} AND id = ${param_idx + 1}",
                *params
            )

            return {"success": True, "message": "BOM updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update BOM")


@router.delete("/{bom_id}", response_model=BOMResponse)
async def delete_bom(request: Request, bom_id: UUID):
    """Delete draft BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM bill_of_materials WHERE tenant_id = $1 AND id = $2 AND status = 'draft'",
                ctx["tenant_id"], bom_id
            )
            if result == "DELETE 0":
                raise HTTPException(status_code=400, detail="BOM not found or not in draft status")

            return {"success": True, "message": "BOM deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete BOM")


@router.post("/{bom_id}/activate", response_model=BOMResponse)
async def activate_bom(request: Request, bom_id: UUID):
    """Activate BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                bom = await conn.fetchrow(
                    "SELECT product_id, status FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"], bom_id
                )
                if not bom:
                    raise HTTPException(status_code=404, detail="BOM not found")

                if bom["status"] != "draft":
                    raise HTTPException(status_code=400, detail="Can only activate draft BOMs")

                # Set all other BOMs for this product to not current
                await conn.execute(
                    """
                    UPDATE bill_of_materials SET is_current = false
                    WHERE tenant_id = $1 AND product_id = $2 AND id != $3
                    """,
                    ctx["tenant_id"], bom["product_id"], bom_id
                )

                # Activate this BOM
                await conn.execute(
                    """
                    UPDATE bill_of_materials
                    SET status = 'active', is_current = true, effective_date = COALESCE(effective_date, CURRENT_DATE)
                    WHERE id = $1
                    """,
                    bom_id
                )

                return {"success": True, "message": "BOM activated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error activating BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to activate BOM")


@router.post("/{bom_id}/obsolete", response_model=BOMResponse)
async def obsolete_bom(request: Request, bom_id: UUID):
    """Mark BOM as obsolete."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE bill_of_materials
                SET status = 'obsolete', is_current = false, obsolete_date = CURRENT_DATE
                WHERE tenant_id = $1 AND id = $2 AND status = 'active'
                """,
                ctx["tenant_id"], bom_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="BOM not found or not active")

            return {"success": True, "message": "BOM marked as obsolete"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking BOM obsolete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark BOM obsolete")


@router.post("/{bom_id}/duplicate", response_model=BOMResponse)
async def duplicate_bom(request: Request, bom_id: UUID, new_code: str = Query(...)):
    """Duplicate BOM to new version."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get original BOM
                original = await conn.fetchrow(
                    "SELECT * FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                    ctx["tenant_id"], bom_id
                )
                if not original:
                    raise HTTPException(status_code=404, detail="BOM not found")

                # Check new code doesn't exist
                exists = await conn.fetchval(
                    "SELECT 1 FROM bill_of_materials WHERE tenant_id = $1 AND bom_code = $2",
                    ctx["tenant_id"], new_code
                )
                if exists:
                    raise HTTPException(status_code=400, detail=f"BOM code '{new_code}' already exists")

                # Create new BOM
                new_version = original["version"] + 1
                new_bom_id = await conn.fetchval(
                    """
                    INSERT INTO bill_of_materials (
                        tenant_id, product_id, bom_code, bom_name, description,
                        version, output_quantity, output_unit, estimated_time_minutes,
                        work_center_id, status, created_by
                    )
                    SELECT $1, product_id, $2, bom_name, description,
                           $3, output_quantity, output_unit, estimated_time_minutes,
                           work_center_id, 'draft', $4
                    FROM bill_of_materials WHERE id = $5
                    RETURNING id
                    """,
                    ctx["tenant_id"], new_code, new_version, ctx["user_id"], bom_id
                )

                # Copy operations
                await conn.execute(
                    """
                    INSERT INTO bom_operations (bom_id, operation_number, operation_name,
                        description, work_center_id, setup_time_minutes, run_time_minutes,
                        labor_rate_per_hour, overhead_rate_per_hour, instructions)
                    SELECT $1, operation_number, operation_name, description, work_center_id,
                           setup_time_minutes, run_time_minutes, labor_rate_per_hour,
                           overhead_rate_per_hour, instructions
                    FROM bom_operations WHERE bom_id = $2
                    """,
                    new_bom_id, bom_id
                )

                # Copy components
                await conn.execute(
                    """
                    INSERT INTO bom_components (bom_id, component_product_id, quantity, unit,
                        wastage_percent, sequence_order, unit_cost, notes)
                    SELECT $1, component_product_id, quantity, unit, wastage_percent,
                           sequence_order, unit_cost, notes
                    FROM bom_components WHERE bom_id = $2
                    """,
                    new_bom_id, bom_id
                )

                # Calculate cost
                await conn.fetchval("SELECT calculate_bom_cost($1)", new_bom_id)

                return {
                    "success": True,
                    "message": f"BOM duplicated as version {new_version}",
                    "data": {"id": str(new_bom_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error duplicating BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to duplicate BOM")


@router.get("/{bom_id}/cost-breakdown", response_model=CostBreakdownResponse)
async def get_cost_breakdown(request: Request, bom_id: UUID):
    """Get detailed cost breakdown."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bom = await conn.fetchrow(
                """
                SELECT bom.*, p.nama_produk as product_name
                FROM bill_of_materials bom
                JOIN products p ON p.id = bom.product_id
                WHERE bom.tenant_id = $1 AND bom.id = $2
                """,
                ctx["tenant_id"], bom_id
            )
            if not bom:
                raise HTTPException(status_code=404, detail="BOM not found")

            # Get material breakdown
            materials = await conn.fetch(
                """
                SELECT p.nama_produk, bc.quantity, bc.unit_cost, bc.extended_cost
                FROM bom_components bc
                JOIN products p ON p.id = bc.component_product_id
                WHERE bc.bom_id = $1
                ORDER BY bc.extended_cost DESC
                """,
                bom_id
            )

            # Get labor breakdown
            labor = await conn.fetch(
                """
                SELECT operation_name,
                       (setup_time_minutes + COALESCE(run_time_minutes, 0)) as total_minutes,
                       labor_rate_per_hour,
                       ((setup_time_minutes + COALESCE(run_time_minutes, 0)) * labor_rate_per_hour / 60) as cost
                FROM bom_operations
                WHERE bom_id = $1
                """,
                bom_id
            )

            total = bom["total_cost"] or 1
            breakdown = []

            # Material items
            for m in materials:
                breakdown.append({
                    "category": "material",
                    "description": m["name"],
                    "quantity": m["quantity"],
                    "unit_cost": m["unit_cost"],
                    "total_cost": m["extended_cost"],
                    "percent_of_total": round(Decimal(m["extended_cost"]) / total * 100, 2)
                })

            # Labor items
            for l in labor:
                breakdown.append({
                    "category": "labor",
                    "description": l["operation_name"],
                    "quantity": Decimal(l["total_minutes"]) / 60,
                    "unit_cost": l["labor_rate_per_hour"],
                    "total_cost": l["cost"] or 0,
                    "percent_of_total": round(Decimal(l["cost"] or 0) / total * 100, 2)
                })

            # Overhead
            if bom["overhead_cost"]:
                breakdown.append({
                    "category": "overhead",
                    "description": "Manufacturing Overhead",
                    "quantity": None,
                    "unit_cost": 0,
                    "total_cost": bom["overhead_cost"],
                    "percent_of_total": round(Decimal(bom["overhead_cost"]) / total * 100, 2)
                })

            unit_cost = int(bom["total_cost"] / float(bom["output_quantity"])) if bom["output_quantity"] else 0

            return {
                "success": True,
                "bom_code": bom["bom_code"],
                "product_name": bom["product_name"],
                "output_quantity": bom["output_quantity"],
                "unit_cost": unit_cost,
                "total_cost": bom["total_cost"],
                "breakdown": breakdown
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cost breakdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cost breakdown")


@router.post("/{bom_id}/recalculate", response_model=BOMResponse)
async def recalculate_cost(request: Request, bom_id: UUID):
    """Recalculate BOM costs."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], bom_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="BOM not found")

            new_cost = await conn.fetchval("SELECT calculate_bom_cost($1)", bom_id)

            return {
                "success": True,
                "message": "Cost recalculated",
                "data": {"total_cost": new_cost}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recalculating cost: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to recalculate cost")


# =============================================================================
# COMPONENTS
# =============================================================================
@router.post("/{bom_id}/components", response_model=BOMResponse)
async def add_component(request: Request, bom_id: UUID, body: BOMComponentInput):
    """Add component to BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bom = await conn.fetchrow(
                "SELECT status FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], bom_id
            )
            if not bom:
                raise HTTPException(status_code=404, detail="BOM not found")

            if bom["status"] != "draft":
                raise HTTPException(status_code=400, detail="Can only modify draft BOMs")

            comp_id = await conn.fetchval(
                """
                INSERT INTO bom_components (
                    bom_id, component_product_id, quantity, unit,
                    wastage_percent, sequence_order, operation_id, unit_cost, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                bom_id, body.component_product_id, body.quantity, body.unit,
                body.wastage_percent, body.sequence_order, body.operation_id,
                body.unit_cost, body.notes
            )

            # Recalculate cost
            await conn.fetchval("SELECT calculate_bom_cost($1)", bom_id)

            return {
                "success": True,
                "message": "Component added",
                "data": {"id": str(comp_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding component: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add component")


@router.delete("/components/{component_id}", response_model=BOMResponse)
async def remove_component(request: Request, component_id: UUID):
    """Remove component from BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get BOM ID and verify access
            comp = await conn.fetchrow(
                """
                SELECT bc.bom_id, bom.status
                FROM bom_components bc
                JOIN bill_of_materials bom ON bom.id = bc.bom_id
                WHERE bc.id = $1 AND bom.tenant_id = $2
                """,
                component_id, ctx["tenant_id"]
            )
            if not comp:
                raise HTTPException(status_code=404, detail="Component not found")

            if comp["status"] != "draft":
                raise HTTPException(status_code=400, detail="Can only modify draft BOMs")

            await conn.execute("DELETE FROM bom_components WHERE id = $1", component_id)
            await conn.fetchval("SELECT calculate_bom_cost($1)", comp["bom_id"])

            return {"success": True, "message": "Component removed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing component: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove component")


# =============================================================================
# OPERATIONS
# =============================================================================
@router.post("/{bom_id}/operations", response_model=BOMResponse)
async def add_operation(request: Request, bom_id: UUID, body: BOMOperationInput):
    """Add operation to BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bom = await conn.fetchrow(
                "SELECT status FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], bom_id
            )
            if not bom:
                raise HTTPException(status_code=404, detail="BOM not found")

            if bom["status"] != "draft":
                raise HTTPException(status_code=400, detail="Can only modify draft BOMs")

            op_id = await conn.fetchval(
                """
                INSERT INTO bom_operations (
                    bom_id, operation_number, operation_name, description,
                    work_center_id, setup_time_minutes, run_time_minutes,
                    labor_rate_per_hour, overhead_rate_per_hour, instructions
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                bom_id, body.operation_number, body.operation_name, body.description,
                body.work_center_id, body.setup_time_minutes, body.run_time_minutes,
                body.labor_rate_per_hour, body.overhead_rate_per_hour, body.instructions
            )

            await conn.fetchval("SELECT calculate_bom_cost($1)", bom_id)

            return {
                "success": True,
                "message": "Operation added",
                "data": {"id": str(op_id)}
            }

    except HTTPException:
        raise
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Operation number already exists")
    except Exception as e:
        logger.error(f"Error adding operation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add operation")


@router.delete("/operations/{operation_id}", response_model=BOMResponse)
async def remove_operation(request: Request, operation_id: UUID):
    """Remove operation from BOM."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            op = await conn.fetchrow(
                """
                SELECT bo.bom_id, bom.status
                FROM bom_operations bo
                JOIN bill_of_materials bom ON bom.id = bo.bom_id
                WHERE bo.id = $1 AND bom.tenant_id = $2
                """,
                operation_id, ctx["tenant_id"]
            )
            if not op:
                raise HTTPException(status_code=404, detail="Operation not found")

            if op["status"] != "draft":
                raise HTTPException(status_code=400, detail="Can only modify draft BOMs")

            await conn.execute("DELETE FROM bom_operations WHERE id = $1", operation_id)
            await conn.fetchval("SELECT calculate_bom_cost($1)", op["bom_id"])

            return {"success": True, "message": "Operation removed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing operation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove operation")


# =============================================================================
# QUERIES
# =============================================================================
@router.get("/products/{product_id}/bom")
async def get_product_bom(request: Request, product_id: UUID):
    """Get current BOM for product."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bom = await conn.fetchrow(
                """
                SELECT id, bom_code, bom_name, version, total_cost
                FROM bill_of_materials
                WHERE tenant_id = $1 AND product_id = $2 AND is_current = true AND status = 'active'
                """,
                ctx["tenant_id"], product_id
            )
            if not bom:
                return {"success": True, "data": None, "message": "No active BOM for product"}

            return {
                "success": True,
                "data": {
                    "id": str(bom["id"]),
                    "bom_code": bom["bom_code"],
                    "bom_name": bom["bom_name"],
                    "version": bom["version"],
                    "total_cost": bom["total_cost"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting product BOM: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get product BOM")


@router.get("/where-used/{product_id}", response_model=WhereUsedResponse)
async def where_used(request: Request, product_id: UUID):
    """Find BOMs where product is used as component."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            product = await conn.fetchrow(
                "SELECT name FROM products WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], product_id
            )
            if not product:
                raise HTTPException(status_code=404, detail="Product not found")

            rows = await conn.fetch(
                """
                SELECT DISTINCT bom.id, bom.bom_code, bom.bom_name, bom.product_id,
                       p.nama_produk as parent_product_name, bc.quantity as quantity_per_unit,
                       bom.is_current, bom.status
                FROM bom_components bc
                JOIN bill_of_materials bom ON bom.id = bc.bom_id
                JOIN products p ON p.id = bom.product_id
                WHERE bc.component_product_id = $1 AND bom.tenant_id = $2
                ORDER BY bom.bom_code
                """,
                product_id, ctx["tenant_id"]
            )

            used_in = [
                {
                    "bom_id": str(row["id"]),
                    "bom_code": row["bom_code"],
                    "bom_name": row["bom_name"],
                    "parent_product_id": str(row["product_id"]),
                    "parent_product_name": row["parent_product_name"],
                    "quantity_per_unit": row["quantity_per_unit"],
                    "is_current": row["is_current"],
                    "status": row["status"],
                }
                for row in rows
            ]

            return {
                "success": True,
                "product_id": str(product_id),
                "product_name": product["name"],
                "used_in": used_in,
                "total_boms": len(used_in)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in where-used query: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to find where product is used")


@router.get("/{bom_id}/materials-required", response_model=MaterialsRequiredResponse)
async def get_materials_required(
    request: Request,
    bom_id: UUID,
    quantity: Decimal = Query(Decimal("1"), gt=0)
):
    """Get materials required for specified quantity."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bom = await conn.fetchrow(
                "SELECT bom_code, output_quantity FROM bill_of_materials WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], bom_id
            )
            if not bom:
                raise HTTPException(status_code=404, detail="BOM not found")

            multiplier = float(quantity) / float(bom["output_quantity"])

            # Get components with inventory
            rows = await conn.fetch(
                """
                SELECT bc.*, p.nama_produk as product_name, p.sku as product_sku,
                       COALESCE(
                           (SELECT SUM(quantity) FROM inventory_ledger WHERE product_id = bc.component_product_id),
                           0
                       ) as available
                FROM bom_components bc
                JOIN products p ON p.id = bc.component_product_id
                WHERE bc.bom_id = $1
                ORDER BY p.nama_produk
                """,
                bom_id
            )

            materials = []
            total_cost = 0
            has_shortage = False

            for row in rows:
                required = float(row["quantity"]) * multiplier * (1 + float(row["wastage_percent"] or 0) / 100)
                line_cost = int(required * row["unit_cost"])
                shortage = required - float(row["available"])

                if shortage > 0:
                    has_shortage = True

                materials.append({
                    "product_id": str(row["component_product_id"]),
                    "product_name": row["product_name"],
                    "product_sku": row["product_sku"],
                    "required_quantity": Decimal(str(round(required, 4))),
                    "unit": row["unit"],
                    "unit_cost": row["unit_cost"],
                    "total_cost": line_cost,
                    "available_quantity": row["available"],
                    "shortage": Decimal(str(round(max(0, shortage), 4)))
                })
                total_cost += line_cost

            return {
                "success": True,
                "bom_code": bom["bom_code"],
                "production_quantity": quantity,
                "materials": materials,
                "total_material_cost": total_cost,
                "has_shortage": has_shortage
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting materials required: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get materials required")
