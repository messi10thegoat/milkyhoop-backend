"""
Router for Kitchen Display System (KDS)
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.kds import (
    BumpItemRequest,
    BumpOrderRequest,
    CreateKDSOrderRequest,
    CreateKDSStationRequest,
    KDSAlertListResponse,
    KDSDisplayResponse,
    KDSMetricsResponse,
    KDSOrderDetailResponse,
    KDSOrderListResponse,
    KDSResponse,
    KDSStationDetailResponse,
    KDSStationListResponse,
    RecallOrderRequest,
    StartItemRequest,
    UpdateKDSStationRequest,
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
    return {"status": "healthy", "service": "kds"}


# =============================================================================
# KDS STATIONS
# =============================================================================

@router.get("/stations", response_model=KDSStationListResponse)
async def list_kds_stations(
    request: Request,
    station_type: Optional[str] = None,
    is_active: Optional[bool] = None
):
    """List KDS stations."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["ks.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if station_type:
            where_clauses.append(f"ks.station_type = ${param_idx}")
            params.append(station_type)
            param_idx += 1

        if is_active is not None:
            where_clauses.append(f"ks.is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        rows = await conn.fetch(f"""
            SELECT
                ks.id, ks.station_code as code, ks.station_name as name, ks.station_type,
                ks.display_columns, ks.auto_bump_minutes,
                ks.alert_threshold_minutes, ks.is_active,
                (SELECT COUNT(*) FROM kds_order_items koi
                 JOIN kds_orders ko ON ko.id = koi.kds_order_id
                 WHERE koi.station_id = ks.id
                 AND koi.status IN ('pending', 'in_progress')
                 AND ko.tenant_id = ks.tenant_id) as pending_orders,
                (SELECT AVG(EXTRACT(EPOCH FROM (koi.completed_at - koi.started_at)))::INT
                 FROM kds_order_items koi
                 WHERE koi.station_id = ks.id AND koi.completed_at IS NOT NULL
                 AND koi.started_at IS NOT NULL) as avg_completion_time
            FROM kds_stations ks
            WHERE {where_sql}
            ORDER BY ks.station_name
        """, *params)

        items = [{
            "id": str(r["id"]),
            "code": r["code"],
            "name": r["name"],
            "station_type": r["station_type"],
            "display_columns": r["display_columns"],
            "auto_bump_minutes": r["auto_bump_minutes"],
            "alert_threshold_minutes": r["alert_threshold_minutes"],
            "sort_order": "fifo",
            "is_active": r["is_active"],
            "pending_orders": r["pending_orders"],
            "avg_completion_time": r["avg_completion_time"]
        } for r in rows]

        return KDSStationListResponse(items=items, total=len(items))


@router.post("/stations", response_model=KDSResponse)
async def create_kds_station(request: Request, data: CreateKDSStationRequest):
    """Create a KDS station."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        exists = await conn.fetchval(
            "SELECT 1 FROM kds_stations WHERE tenant_id = $1 AND code = $2",
            ctx["tenant_id"], data.code
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"Station code {data.code} already exists")

        row = await conn.fetchrow("""
            INSERT INTO kds_stations (
                tenant_id, code, name, station_type, display_columns,
                auto_bump_minutes, alert_threshold_minutes, sort_order,
                created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
        """,
            ctx["tenant_id"], data.code, data.name, data.station_type,
            data.display_columns, data.auto_bump_minutes,
            data.alert_threshold_minutes, 'fifo', ctx["user_id"]
        )

        return KDSResponse(
            success=True,
            message="KDS station created",
            data={"id": str(row["id"])}
        )


@router.put("/stations/{station_id}", response_model=KDSResponse)
async def update_kds_station(
    request: Request,
    station_id: UUID,
    data: UpdateKDSStationRequest
):
    """Update a KDS station."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM kds_stations WHERE id = $1 AND tenant_id = $2",
            station_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Station not found")

        updates = []
        params = []
        param_idx = 1

        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ${param_idx}")
            params.append(value)
            param_idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(station_id)
        await conn.execute(f"""
            UPDATE kds_stations
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
        """, *params)

        return KDSResponse(success=True, message="Station updated")


@router.get("/stations/{station_id}", response_model=KDSStationDetailResponse)
async def get_kds_station(request: Request, station_id: UUID):
    """Get KDS station details."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        station = await conn.fetchrow("""
            SELECT * FROM kds_stations
            WHERE id = $1 AND tenant_id = $2
        """, station_id, ctx["tenant_id"])

        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        # Get category filters
        filters = await conn.fetch("""
            SELECT mc.code
            FROM kds_station_categories ksc
            JOIN menu_categories mc ON mc.id = ksc.category_id
            WHERE ksc.station_id = $1
        """, station_id)

        return KDSStationDetailResponse(
            success=True,
            id=str(station["id"]),
            code=station["code"],
            name=station["name"],
            station_type=station["station_type"],
            display_columns=station["display_columns"],
            auto_bump_minutes=station["auto_bump_minutes"],
            alert_threshold_minutes=station["alert_threshold_minutes"],
            sort_order='fifo',
            is_active=station["is_active"],
            category_filters=[f["code"] for f in filters]
        )


# =============================================================================
# KDS ORDERS
# =============================================================================

@router.get("/orders", response_model=KDSOrderListResponse)
async def list_kds_orders(
    request: Request,
    status: Optional[str] = None,
    station_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List KDS orders."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["ko.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"ko.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if station_id:
            where_clauses.append(f"""
                EXISTS (SELECT 1 FROM kds_order_items koi
                        WHERE koi.kds_order_id = ko.id AND koi.station_id = ${param_idx})
            """)
            params.append(station_id)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM kds_orders ko WHERE {where_sql}", *params
        )

        params.extend([limit, offset])
        rows = await conn.fetch(f"""
            SELECT
                ko.id, ko.order_number, ko.order_type as source_type, ko.sales_invoice_id::text as source_reference,
                rt.table_number, ko.customer_name as server_name, ko.status, ko.priority,
                ko.created_at,
                (SELECT COUNT(*) FROM kds_order_items koi WHERE koi.kds_order_id = ko.id) as item_count,
                (SELECT COUNT(*) FROM kds_order_items koi
                 WHERE koi.kds_order_id = ko.id AND koi.status = 'pending') as items_pending,
                (SELECT COUNT(*) FROM kds_order_items koi
                 WHERE koi.kds_order_id = ko.id AND koi.status = 'completed') as items_completed,
                EXTRACT(EPOCH FROM (NOW() - ko.created_at))::INT as elapsed_seconds
            FROM kds_orders ko
            LEFT JOIN restaurant_tables rt ON rt.id = ko.table_id
            WHERE {where_sql}
            ORDER BY
                ko.priority ASC,
                ko.created_at
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """, *params)

        items = []
        for r in rows:
            elapsed = r["elapsed_seconds"] or 0
            # Get alert threshold from any station (simplified)
            is_overdue = elapsed > (10 * 60)  # Default 10 minutes

            items.append({
                "id": str(r["id"]),
                "order_number": r["order_number"],
                "source_type": r["source_type"],
                "source_reference": r["source_reference"],
                "table_number": r["table_number"],
                "server_name": r["server_name"],
                "status": r["status"],
                "priority": r["priority"],
                "item_count": r["item_count"],
                "items_pending": r["items_pending"],
                "items_completed": r["items_completed"],
                "created_at": r["created_at"],
                "elapsed_seconds": elapsed,
                "is_overdue": is_overdue
            })

        return KDSOrderListResponse(items=items, total=total)


@router.post("/orders", response_model=KDSResponse)
async def create_kds_order(request: Request, data: CreateKDSOrderRequest):
    """Create a KDS order (typically from POS)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Generate order number
            order_number = await conn.fetchval(
                "SELECT generate_kds_order_number($1)",
                ctx["tenant_id"]
            )

            # Determine overall priority (highest from items)
            priority = "normal"
            for item in data.items:
                if item.priority == "fire":
                    priority = "fire"
                    break
                elif item.priority == "rush" and priority != "fire":
                    priority = "rush"

            # Create order
            order = await conn.fetchrow("""
                INSERT INTO kds_orders (
                    tenant_id, order_number, source_type, source_reference,
                    table_id, server_name, priority, notes, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """,
                ctx["tenant_id"], order_number, data.source_type,
                data.source_reference, data.table_id, data.server_name,
                priority, data.notes, ctx["user_id"]
            )

            order_id = order["id"]

            # Add items and route to stations
            for item_data in data.items:
                # Get menu item info
                menu_item = await conn.fetchrow("""
                    SELECT mi.id, mi.name, mi.category_id
                    FROM menu_items mi
                    WHERE mi.id = $1 AND mi.tenant_id = $2
                """, item_data.menu_item_id, ctx["tenant_id"])

                if not menu_item:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Menu item {item_data.menu_item_id} not found"
                    )

                # Find station for this category
                station_id = await conn.fetchval("""
                    SELECT ks.id FROM kds_stations ks
                    LEFT JOIN kds_station_categories ksc ON ksc.station_id = ks.id
                    WHERE ks.tenant_id = $1 AND ks.is_active = true
                    AND (ksc.category_id = $2 OR ksc.category_id IS NULL)
                    ORDER BY ksc.category_id DESC NULLS LAST
                    LIMIT 1
                """, ctx["tenant_id"], menu_item["category_id"])

                await conn.execute("""
                    INSERT INTO kds_order_items (
                        kds_order_id, menu_item_id, quantity,
                        modifiers, notes, priority, station_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                    order_id, item_data.menu_item_id, item_data.quantity,
                    item_data.modifiers, item_data.notes, item_data.priority,
                    station_id
                )

            return KDSResponse(
                success=True,
                message="KDS order created",
                data={
                    "id": str(order_id),
                    "order_number": order_number
                }
            )


@router.get("/orders/{order_id}", response_model=KDSOrderDetailResponse)
async def get_kds_order(request: Request, order_id: UUID):
    """Get KDS order details."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        order = await conn.fetchrow("""
            SELECT ko.*, rt.table_number
            FROM kds_orders ko
            LEFT JOIN restaurant_tables rt ON rt.id = ko.table_id
            WHERE ko.id = $1 AND ko.tenant_id = $2
        """, order_id, ctx["tenant_id"])

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        items = await conn.fetch("""
            SELECT
                koi.id, koi.menu_item_id, mi.name as menu_item_name,
                koi.quantity, koi.notes, koi.modifiers, koi.priority,
                koi.status, koi.station_id, ks.station_name as station_name,
                koi.started_at, koi.completed_at,
                CASE WHEN koi.started_at IS NOT NULL
                     THEN EXTRACT(EPOCH FROM (COALESCE(koi.completed_at, NOW()) - koi.started_at))::INT
                     ELSE NULL END as elapsed_seconds
            FROM kds_order_items koi
            JOIN menu_items mi ON mi.id = koi.menu_item_id
            LEFT JOIN kds_stations ks ON ks.id = koi.station_id
            WHERE koi.kds_order_id = $1
            ORDER BY koi.id
        """, order_id)

        elapsed = int((datetime.now() - order["created_at"]).total_seconds()) if order["created_at"] else 0

        return KDSOrderDetailResponse(
            success=True,
            id=str(order["id"]),
            order_number=order["order_number"],
            source_type=order["source_type"],
            source_reference=order["source_reference"],
            table_id=str(order["table_id"]) if order["table_id"] else None,
            table_number=order["table_number"],
            server_name=order["server_name"],
            status=order["status"],
            items=[{
                "id": str(i["id"]),
                "menu_item_id": str(i["menu_item_id"]),
                "menu_item_name": i["menu_item_name"],
                "quantity": i["quantity"],
                "notes": i["notes"],
                "modifiers": i["modifiers"],
                "priority": i["priority"],
                "status": i["status"],
                "station_id": str(i["station_id"]) if i["station_id"] else None,
                "station_name": i["station_name"],
                "started_at": i["started_at"],
                "completed_at": i["completed_at"],
                "elapsed_seconds": i["elapsed_seconds"]
            } for i in items],
            notes=order["notes"],
            created_at=order["created_at"],
            started_at=order["started_at"],
            completed_at=order["completed_at"],
            total_elapsed_seconds=elapsed
        )


# =============================================================================
# KDS DISPLAY (For Kitchen Screens)
# =============================================================================

@router.get("/display/{station_id}", response_model=KDSDisplayResponse)
async def get_kds_display(request: Request, station_id: UUID):
    """Get display data for a KDS station screen."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        station = await conn.fetchrow("""
            SELECT id, code, name, station_type, alert_threshold_minutes, sort_order
            FROM kds_stations
            WHERE id = $1 AND tenant_id = $2
        """, station_id, ctx["tenant_id"])

        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        alert_seconds = station["alert_threshold_minutes"] * 60

        # Get orders with items for this station
        orders_query = """
            SELECT DISTINCT ko.id, ko.order_number, ko.priority,
                   rt.table_number, ko.server_name, ko.created_at
            FROM kds_orders ko
            JOIN kds_order_items koi ON koi.kds_order_id = ko.id
            LEFT JOIN restaurant_tables rt ON rt.id = ko.table_id
            WHERE ko.tenant_id = $1
            AND ko.status IN ('pending', 'in_progress')
            AND koi.station_id = $2
            AND koi.status IN ('pending', 'in_progress')
        """

        if 'fifo' == "priority":
            orders_query += """
                ORDER BY
                    ko.priority ASC,
                    ko.created_at
            """
        elif 'fifo' == "table":
            orders_query += " ORDER BY rt.table_number NULLS LAST, ko.created_at"
        else:
            orders_query += " ORDER BY ko.created_at"

        orders = await conn.fetch(orders_query, ctx["tenant_id"], station_id)

        display_orders = []
        for order in orders:
            elapsed = int((datetime.now() - order["created_at"]).total_seconds())

            # Determine alert level
            if elapsed > alert_seconds * 1.5:
                alert_level = "critical"
            elif elapsed > alert_seconds:
                alert_level = "warning"
            else:
                alert_level = "normal"

            # Get items for this order at this station
            items = await conn.fetch("""
                SELECT
                    koi.id, mi.name, koi.quantity, koi.modifiers,
                    koi.notes, koi.priority, koi.status,
                    CASE WHEN koi.started_at IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (NOW() - koi.started_at))::INT
                         ELSE 0 END as elapsed_seconds
                FROM kds_order_items koi
                JOIN menu_items mi ON mi.id = koi.menu_item_id
                WHERE koi.kds_order_id = $1 AND koi.station_id = $2
                AND koi.status IN ('pending', 'in_progress')
            """, order["id"], station_id)

            display_orders.append({
                "order_id": str(order["id"]),
                "order_number": order["order_number"],
                "table_number": order["table_number"],
                "server_name": order["server_name"],
                "priority": order["priority"],
                "items": [{
                    "item_id": str(i["id"]),
                    "name": i["name"],
                    "quantity": i["quantity"],
                    "modifiers": i["modifiers"],
                    "notes": i["notes"],
                    "priority": i["priority"],
                    "status": i["status"],
                    "elapsed_seconds": i["elapsed_seconds"]
                } for i in items],
                "created_at": order["created_at"],
                "elapsed_seconds": elapsed,
                "alert_level": alert_level
            })

        return KDSDisplayResponse(
            success=True,
            station_id=str(station["id"]),
            station_name=station["name"],
            station_type=station["station_type"],
            orders=display_orders,
            total_pending=len(display_orders),
            timestamp=datetime.now()
        )


# =============================================================================
# KDS ACTIONS
# =============================================================================

@router.post("/bump-item", response_model=KDSResponse)
async def bump_item(request: Request, data: BumpItemRequest):
    """Mark an item as completed (bump from screen)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Update item
            item = await conn.fetchrow("""
                UPDATE kds_order_items
                SET status = 'completed', completed_at = NOW()
                WHERE id = $1
                AND kds_order_id IN (SELECT id FROM kds_orders WHERE tenant_id = $2)
                RETURNING kds_order_id
            """, data.item_id, ctx["tenant_id"])

            if not item:
                raise HTTPException(status_code=404, detail="Item not found")

            # Check if all items are completed
            pending = await conn.fetchval("""
                SELECT COUNT(*) FROM kds_order_items
                WHERE kds_order_id = $1 AND status != 'completed'
            """, item["kds_order_id"])

            if pending == 0:
                await conn.execute("""
                    UPDATE kds_orders
                    SET status = 'completed', completed_at = NOW()
                    WHERE id = $1
                """, item["kds_order_id"])

            # Record history
            await conn.execute("""
                INSERT INTO kds_order_history (
                    kds_order_id, action, item_id, performed_by
                )
                VALUES ($1, 'bump_item', $2, $3)
            """, item["kds_order_id"], data.item_id, ctx["user_id"])

            return KDSResponse(success=True, message="Item bumped")


@router.post("/bump-order", response_model=KDSResponse)
async def bump_order(request: Request, data: BumpOrderRequest):
    """Mark entire order as completed."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Update all items
            await conn.execute("""
                UPDATE kds_order_items
                SET status = 'completed', completed_at = NOW()
                WHERE kds_order_id = $1 AND status != 'completed'
            """, data.order_id)

            # Update order
            updated = await conn.fetchval("""
                UPDATE kds_orders
                SET status = 'completed', completed_at = NOW()
                WHERE id = $1 AND tenant_id = $2
                RETURNING id
            """, data.order_id, ctx["tenant_id"])

            if not updated:
                raise HTTPException(status_code=404, detail="Order not found")

            # Record history
            await conn.execute("""
                INSERT INTO kds_order_history (
                    kds_order_id, action, performed_by
                )
                VALUES ($1, 'bump_order', $2)
            """, data.order_id, ctx["user_id"])

            return KDSResponse(success=True, message="Order bumped")


@router.post("/recall", response_model=KDSResponse)
async def recall_order(request: Request, data: RecallOrderRequest):
    """Recall a completed order back to display."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Reopen order
            updated = await conn.fetchval("""
                UPDATE kds_orders
                SET status = 'in_progress', completed_at = NULL
                WHERE id = $1 AND tenant_id = $2
                RETURNING id
            """, data.order_id, ctx["tenant_id"])

            if not updated:
                raise HTTPException(status_code=404, detail="Order not found")

            # Reopen items
            await conn.execute("""
                UPDATE kds_order_items
                SET status = 'in_progress', completed_at = NULL
                WHERE kds_order_id = $1
            """, data.order_id)

            # Record history
            await conn.execute("""
                INSERT INTO kds_order_history (
                    kds_order_id, action, notes, performed_by
                )
                VALUES ($1, 'recall', $2, $3)
            """, data.order_id, data.reason, ctx["user_id"])

            return KDSResponse(success=True, message="Order recalled")


@router.post("/start-item", response_model=KDSResponse)
async def start_item(request: Request, data: StartItemRequest):
    """Mark item as started (in progress)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            item = await conn.fetchrow("""
                UPDATE kds_order_items
                SET status = 'in_progress', started_at = NOW(), station_id = $3
                WHERE id = $1
                AND kds_order_id IN (SELECT id FROM kds_orders WHERE tenant_id = $2)
                RETURNING kds_order_id
            """, data.item_id, ctx["tenant_id"], data.station_id)

            if not item:
                raise HTTPException(status_code=404, detail="Item not found")

            # Update order status if first item started
            await conn.execute("""
                UPDATE kds_orders
                SET status = 'in_progress', started_at = COALESCE(started_at, NOW())
                WHERE id = $1 AND status = 'pending'
            """, item["kds_order_id"])

            return KDSResponse(success=True, message="Item started")


# =============================================================================
# KDS ALERTS
# =============================================================================

@router.get("/alerts", response_model=KDSAlertListResponse)
async def list_kds_alerts(
    request: Request,
    station_id: Optional[UUID] = None,
    acknowledged: Optional[bool] = None
):
    """List KDS alerts."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["ka.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if station_id:
            where_clauses.append(f"ka.station_id = ${param_idx}")
            params.append(station_id)
            param_idx += 1

        if acknowledged is not None:
            if acknowledged:
                where_clauses.append("ka.acknowledged_at IS NOT NULL")
            else:
                where_clauses.append("ka.acknowledged_at IS NULL")

        where_sql = " AND ".join(where_clauses)

        rows = await conn.fetch(f"""
            SELECT
                ka.id, ka.station_id, ks.station_name as station_name,
                ka.alert_type, ka.severity, ka.message,
                ka.kds_order_id, ka.item_id,
                ka.created_at, ka.acknowledged_at
            FROM kds_alerts ka
            LEFT JOIN kds_stations ks ON ks.id = ka.station_id
            WHERE {where_sql}
            ORDER BY ka.created_at DESC
            LIMIT 100
        """, *params)

        unacked = sum(1 for r in rows if r["acknowledged_at"] is None)

        items = [{
            "id": str(r["id"]),
            "station_id": str(r["station_id"]) if r["station_id"] else None,
            "station_name": r["station_name"],
            "alert_type": r["alert_type"],
            "severity": r["severity"],
            "message": r["message"],
            "order_id": str(r["kds_order_id"]) if r["kds_order_id"] else None,
            "item_id": str(r["item_id"]) if r["item_id"] else None,
            "created_at": r["created_at"],
            "acknowledged_at": r["acknowledged_at"]
        } for r in rows]

        return KDSAlertListResponse(
            items=items,
            total=len(items),
            unacknowledged=unacked
        )


@router.post("/alerts/{alert_id}/acknowledge", response_model=KDSResponse)
async def acknowledge_alert(request: Request, alert_id: UUID):
    """Acknowledge an alert."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        updated = await conn.fetchval("""
            UPDATE kds_alerts
            SET acknowledged_at = NOW(), acknowledged_by = $3
            WHERE id = $1 AND tenant_id = $2
            RETURNING id
        """, alert_id, ctx["tenant_id"], ctx["user_id"])

        if not updated:
            raise HTTPException(status_code=404, detail="Alert not found")

        return KDSResponse(success=True, message="Alert acknowledged")


# =============================================================================
# KDS METRICS
# =============================================================================

@router.get("/metrics", response_model=KDSMetricsResponse)
async def get_kds_metrics(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get KDS performance metrics."""
    ctx = get_user_context(request)
    pool = await get_pool()

    from datetime import date as date_type

    if not start_date:
        start = date_type.today() - timedelta(days=7)
    else:
        start = date_type.fromisoformat(start_date)

    if not end_date:
        end = date_type.today()
    else:
        end = date_type.fromisoformat(end_date)

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        # Get daily metrics
        daily_rows = await conn.fetch("""
            SELECT
                DATE(ko.created_at) as date,
                COUNT(DISTINCT ko.id) as total_orders,
                COUNT(koi.id) as total_items,
                AVG(EXTRACT(EPOCH FROM (ko.completed_at - ko.created_at)))::INT as avg_ticket_time_seconds
            FROM kds_orders ko
            JOIN kds_order_items koi ON koi.kds_order_id = ko.id
            WHERE ko.tenant_id = $1
            AND DATE(ko.created_at) BETWEEN $2 AND $3
            AND ko.status = 'completed'
            GROUP BY DATE(ko.created_at)
            ORDER BY DATE(ko.created_at)
        """, ctx["tenant_id"], start, end)

        # Get station metrics
        station_rows = await conn.fetch("""
            SELECT
                ks.id as station_id, ks.station_name as station_name,
                COUNT(DISTINCT koi.kds_order_id) as orders_completed,
                COUNT(koi.id) as items_completed,
                AVG(EXTRACT(EPOCH FROM (koi.completed_at - koi.started_at)))::INT as avg_item_time_seconds
            FROM kds_stations ks
            LEFT JOIN kds_order_items koi ON koi.station_id = ks.id
            JOIN kds_orders ko ON ko.id = koi.kds_order_id
            WHERE ks.tenant_id = $1
            AND koi.completed_at IS NOT NULL
            AND DATE(koi.completed_at) BETWEEN $2 AND $3
            GROUP BY ks.id, ks.station_name
        """, ctx["tenant_id"], start, end)

        metrics = []
        for d in daily_rows:
            # Get peak hour for this day
            peak = await conn.fetchrow("""
                SELECT EXTRACT(HOUR FROM created_at)::INT as hour, COUNT(*) as count
                FROM kds_orders
                WHERE tenant_id = $1 AND DATE(created_at) = $2
                GROUP BY EXTRACT(HOUR FROM created_at)
                ORDER BY COUNT(*) DESC
                LIMIT 1
            """, ctx["tenant_id"], d["date"])

            metrics.append({
                "date": str(d["date"]),
                "total_orders": d["total_orders"],
                "total_items": d["total_items"],
                "avg_ticket_time_seconds": d["avg_ticket_time_seconds"] or 0,
                "peak_hour": peak["hour"] if peak else 0,
                "peak_hour_orders": peak["count"] if peak else 0,
                "stations": [{
                    "station_id": str(s["station_id"]),
                    "station_name": s["station_name"],
                    "orders_completed": s["orders_completed"],
                    "items_completed": s["items_completed"],
                    "avg_ticket_time_seconds": 0,
                    "avg_item_time_seconds": s["avg_item_time_seconds"] or 0,
                    "overdue_count": 0,
                    "recall_count": 0
                } for s in station_rows]
            })

        # Summary
        total_orders = sum(m["total_orders"] for m in metrics)
        total_items = sum(m["total_items"] for m in metrics)
        avg_time = sum(m["avg_ticket_time_seconds"] for m in metrics) / len(metrics) if metrics else 0

        return KDSMetricsResponse(
            success=True,
            period_start=str(start),
            period_end=str(end),
            metrics=metrics,
            summary={
                "total_orders": total_orders,
                "total_items": total_items,
                "avg_ticket_time_seconds": int(avg_time)
            }
        )
