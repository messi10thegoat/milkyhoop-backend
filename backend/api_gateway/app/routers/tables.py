"""
Router for Table Management (Manajemen Meja Restoran)
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.tables import (
    CreateReservationRequest,
    CreateTableAreaRequest,
    CreateTableRequest,
    CreateTableSessionRequest,
    CreateWaitlistEntryRequest,
    FloorPlanResponse,
    ReservationDetailResponse,
    ReservationListResponse,
    TableAreaListResponse,
    TableAvailabilityRequest,
    TableAvailabilityResponse,
    TableDetailResponse,
    TableListResponse,
    TableResponse,
    TableSessionDetailResponse,
    TableSessionListResponse,
    TableStatsResponse,
    UpdateReservationRequest,
    UpdateTableAreaRequest,
    UpdateTableRequest,
    UpdateTableSessionRequest,
    WaitlistResponse,
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
    return {"status": "healthy", "service": "tables"}


@router.get("", response_model=TableListResponse)
async def list_tables_root(
    request: Request,
    area_id: Optional[UUID] = None,
    status: Optional[str] = None,
    is_active: Optional[bool] = None
):
    """List restaurant tables (root endpoint)."""
    return await list_tables(request, area_id, status, is_active)


# =============================================================================
# TABLE AREAS
# =============================================================================

@router.get("/areas", response_model=TableAreaListResponse)
async def list_table_areas(
    request: Request,
    is_active: Optional[bool] = None
):
    """List table areas."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["ta.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if is_active is not None:
            where_clauses.append(f"ta.is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        rows = await conn.fetch(f"""
            SELECT
                ta.id, ta.area_code as code, ta.area_name as name, ta.description,
                ta.floor_number, ta.is_outdoor, ta.is_smoking,
                ta.display_order, ta.is_active,
                (SELECT COUNT(*) FROM restaurant_tables rt WHERE rt.area_id = ta.id) as table_count,
                (SELECT COUNT(*) FROM restaurant_tables rt
                 WHERE rt.area_id = ta.id AND rt.is_active = true
                 AND NOT EXISTS (SELECT 1 FROM table_sessions ts
                                 WHERE ts.table_id = rt.id AND ts.vacated_at IS NULL)) as available_count
            FROM table_areas ta
            WHERE {where_sql}
            ORDER BY ta.display_order, ta.area_name
        """, *params)

        items = [{
            "id": str(r["id"]),
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "floor_number": r["floor_number"],
            "is_outdoor": r["is_outdoor"],
            "is_smoking": r["is_smoking"],
            "display_order": r["display_order"],
            "is_active": r["is_active"],
            "table_count": r["table_count"],
            "available_count": r["available_count"]
        } for r in rows]

        return TableAreaListResponse(items=items, total=len(items))


@router.post("/areas", response_model=TableResponse)
async def create_table_area(request: Request, data: CreateTableAreaRequest):
    """Create a table area."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        exists = await conn.fetchval(
            "SELECT 1 FROM table_areas WHERE tenant_id = $1 AND code = $2",
            ctx["tenant_id"], data.code
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"Area code {data.code} already exists")

        row = await conn.fetchrow("""
            INSERT INTO table_areas (
                tenant_id, code, name, description, floor_number,
                is_outdoor, is_smoking, display_order, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
        """,
            ctx["tenant_id"], data.code, data.name, data.description,
            data.floor_number, data.is_outdoor, data.is_smoking,
            data.display_order, ctx["user_id"]
        )

        return TableResponse(
            success=True,
            message="Table area created",
            data={"id": str(row["id"])}
        )


@router.put("/areas/{area_id}", response_model=TableResponse)
async def update_table_area(
    request: Request,
    area_id: UUID,
    data: UpdateTableAreaRequest
):
    """Update a table area."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM table_areas WHERE id = $1 AND tenant_id = $2",
            area_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Area not found")

        updates = []
        params = []
        param_idx = 1

        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ${param_idx}")
            params.append(value)
            param_idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(area_id)
        await conn.execute(f"""
            UPDATE table_areas
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
        """, *params)

        return TableResponse(success=True, message="Area updated")


# =============================================================================
# TABLES
# =============================================================================

@router.get("/tables", response_model=TableListResponse)
async def list_tables(
    request: Request,
    area_id: Optional[UUID] = None,
    status: Optional[str] = None,
    is_active: Optional[bool] = None
):
    """List restaurant tables."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["rt.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if area_id:
            where_clauses.append(f"rt.area_id = ${param_idx}")
            params.append(area_id)
            param_idx += 1

        if is_active is not None:
            where_clauses.append(f"rt.is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        rows = await conn.fetch(f"""
            SELECT
                rt.id, rt.table_number, rt.area_id, ta.area_name,
                rt.min_capacity, rt.max_capacity, rt.shape as table_shape,
                true as is_reservable, rt.is_active,
                ts.id as current_session_id, ts.guest_count as current_guests,
                CASE WHEN ts.id IS NOT NULL
                     THEN EXTRACT(EPOCH FROM (NOW() - ts.seated_at))::INT / 60
                     ELSE NULL END as session_duration_minutes,
                CASE
                    WHEN ts.vacated_at IS NULL THEN 'occupied'
                    WHEN EXISTS (SELECT 1 FROM table_reservations r
                                 WHERE r.table_id = rt.id
                                 AND r.reservation_date = CURRENT_DATE
                                 AND r.status = 'confirmed'
                                 AND r.reservation_time <= NOW()::TIME
                                 AND r.reservation_time + (r.duration_minutes || ' minutes')::INTERVAL > NOW()::TIME) THEN 'reserved'
                    WHEN NOT rt.is_active THEN 'blocked'
                    ELSE 'available'
                END as status
            FROM restaurant_tables rt
            JOIN table_areas ta ON ta.id = rt.area_id
            LEFT JOIN table_sessions ts ON ts.table_id = rt.id AND ts.vacated_at IS NULL
            WHERE {where_sql}
            ORDER BY ta.display_order, rt.table_number
        """, *params)

        items = rows

        # Filter by status if requested
        if status:
            items = [r for r in rows if r["status"] == status]

        return TableListResponse(
            items=[{
                "id": str(r["id"]),
                "table_number": r["table_number"],
                "area_id": str(r["area_id"]),
                "area_name": r["area_name"],
                "capacity_min": r["min_capacity"],
                "capacity_max": r["max_capacity"],
                "table_shape": r["table_shape"],
                "status": r["status"],
                "current_session_id": str(r["current_session_id"]) if r["current_session_id"] else None,
                "current_guests": r["current_guests"],
                "session_duration_minutes": r["session_duration_minutes"],
                "is_reservable": r["is_reservable"],
                "is_active": r["is_active"]
            } for r in items],
            total=len(items)
        )


@router.post("/tables", response_model=TableResponse)
async def create_table(request: Request, data: CreateTableRequest):
    """Create a restaurant table."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        exists = await conn.fetchval(
            "SELECT 1 FROM restaurant_tables WHERE tenant_id = $1 AND table_number = $2",
            ctx["tenant_id"], data.table_number
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"Table {data.table_number} already exists")

        row = await conn.fetchrow("""
            INSERT INTO restaurant_tables (
                tenant_id, table_number, area_id, min_capacity, max_capacity,
                shape, position_x, position_y, is_reservable, is_combinable,
                created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
        """,
            ctx["tenant_id"], data.table_number, data.area_id,
            data.capacity_min, data.capacity_max, data.table_shape,
            data.position_x, data.position_y, data.is_reservable,
            data.is_combinable, ctx["user_id"]
        )

        return TableResponse(
            success=True,
            message="Table created",
            data={"id": str(row["id"])}
        )


@router.get("/tables/{table_id}", response_model=TableDetailResponse)
async def get_table(request: Request, table_id: UUID):
    """Get table details."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        table = await conn.fetchrow("""
            SELECT rt.*, ta.area_name
            FROM restaurant_tables rt
            JOIN table_areas ta ON ta.id = rt.area_id
            WHERE rt.id = $1 AND rt.tenant_id = $2
        """, table_id, ctx["tenant_id"])

        if not table:
            raise HTTPException(status_code=404, detail="Table not found")

        # Get current session
        session = await conn.fetchrow("""
            SELECT ts.*, u.name as server_name
            FROM table_sessions ts
            LEFT JOIN users u ON u.id = ts.server_id
            WHERE ts.table_id = $1 AND ts.vacated_at IS NULL
        """, table_id)

        # Get upcoming reservations
        reservations = await conn.fetch("""
            SELECT id, reservation_number, reservation_date, reservation_time,
                   party_size, customer_name, duration_minutes
            FROM table_reservations
            WHERE table_id = $1 AND status = 'confirmed'
            AND (reservation_date > CURRENT_DATE
                 OR (reservation_date = CURRENT_DATE AND reservation_time > NOW()::TIME))
            ORDER BY reservation_date, reservation_time
            LIMIT 5
        """, table_id)

        # Determine status
        if session:
            status = "occupied"
        elif not table["is_active"]:
            status = "blocked"
        else:
            status = "available"

        return TableDetailResponse(
            success=True,
            id=str(table["id"]),
            table_number=table["table_number"],
            area_id=str(table["area_id"]),
            area_name=table["area_name"],
            capacity_min=table["capacity_min"],
            capacity_max=table["capacity_max"],
            table_shape=table["table_shape"],
            position_x=table["position_x"],
            position_y=table["position_y"],
            status=status,
            is_reservable=table["is_reservable"],
            is_combinable=table["is_combinable"],
            is_active=table["is_active"],
            current_session={
                "id": str(session["id"]),
                "guest_count": session["guest_count"],
                "server_name": session["server_name"],
                "started_at": session["started_at"].isoformat(),
                "duration_minutes": int((datetime.now() - session["started_at"]).total_seconds() / 60)
            } if session else None,
            upcoming_reservations=[{
                "id": str(r["id"]),
                "reservation_number": r["reservation_number"],
                "date": str(r["reservation_date"]),
                "time": str(r["reservation_time"]),
                "party_size": r["party_size"],
                "customer_name": r["customer_name"]
            } for r in reservations]
        )


@router.put("/tables/{table_id}", response_model=TableResponse)
async def update_table(request: Request, table_id: UUID, data: UpdateTableRequest):
    """Update a table."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM restaurant_tables WHERE id = $1 AND tenant_id = $2",
            table_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Table not found")

        updates = []
        params = []
        param_idx = 1

        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ${param_idx}")
            params.append(value)
            param_idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(table_id)
        await conn.execute(f"""
            UPDATE restaurant_tables
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
        """, *params)

        return TableResponse(success=True, message="Table updated")


# =============================================================================
# RESERVATIONS
# =============================================================================

@router.get("/reservations", response_model=ReservationListResponse)
async def list_reservations(
    request: Request,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List reservations."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["r.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if date_from:
            where_clauses.append(f"r.reservation_date >= ${param_idx}")
            params.append(date_from)
            param_idx += 1

        if date_to:
            where_clauses.append(f"r.reservation_date <= ${param_idx}")
            params.append(date_to)
            param_idx += 1

        if status:
            where_clauses.append(f"r.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM table_reservations r WHERE {where_sql}", *params
        )

        params.extend([limit, offset])
        rows = await conn.fetch(f"""
            SELECT
                r.id, r.reservation_number, r.reservation_date, r.reservation_time,
                r.party_size, r.customer_name, r.customer_phone,
                r.table_id, rt.table_number, ta.area_name,
                r.status, r.duration_minutes, r.occasion, r.created_at
            FROM table_reservations r
            LEFT JOIN restaurant_tables rt ON rt.id = r.table_id
            LEFT JOIN table_areas ta ON ta.id = rt.area_id
            WHERE {where_sql}
            ORDER BY r.reservation_date, r.reservation_time
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """, *params)

        items = [{
            "id": str(r["id"]),
            "reservation_number": r["reservation_number"],
            "reservation_date": r["reservation_date"],
            "reservation_time": r["reservation_time"],
            "party_size": r["party_size"],
            "customer_name": r["customer_name"],
            "customer_phone": r["customer_phone"],
            "table_id": str(r["table_id"]) if r["table_id"] else None,
            "table_number": r["table_number"],
            "area_name": r["area_name"],
            "status": r["status"],
            "duration_minutes": r["duration_minutes"],
            "occasion": r["occasion"],
            "created_at": r["created_at"]
        } for r in rows]

        return ReservationListResponse(
            items=items,
            total=total,
            has_more=(offset + len(items)) < total
        )


@router.post("/reservations", response_model=TableResponse)
async def create_reservation(request: Request, data: CreateReservationRequest):
    """Create a reservation."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Generate reservation number
            res_number = await conn.fetchval(
                "SELECT generate_reservation_number($1)",
                ctx["tenant_id"]
            )

            # Auto-assign table if not specified
            table_id = data.table_id
            if not table_id and data.area_preference:
                # Find available table
                table_id = await conn.fetchval("""
                    SELECT rt.id FROM restaurant_tables rt
                    WHERE rt.tenant_id = $1
                    AND rt.area_id = $2
                    AND rt.is_active = true
                    AND true as is_reservable = true
                    AND rt.max_capacity >= $3
                    AND NOT EXISTS (
                        SELECT 1 FROM table_reservations r
                        WHERE r.table_id = rt.id
                        AND r.reservation_date = $4
                        AND r.status = 'confirmed'
                        AND (
                            (r.reservation_time <= $5 AND r.reservation_time + (r.duration_minutes || ' minutes')::INTERVAL > $5)
                            OR
                            ($5 <= r.reservation_time AND $5 + ($6 || ' minutes')::INTERVAL > r.reservation_time)
                        )
                    )
                    ORDER BY rt.max_capacity
                    LIMIT 1
                """,
                    ctx["tenant_id"], data.area_preference, data.party_size,
                    data.reservation_date, data.reservation_time, data.duration_minutes
                )

            row = await conn.fetchrow("""
                INSERT INTO reservations (
                    tenant_id, reservation_number, reservation_date, reservation_time,
                    party_size, customer_name, customer_phone, customer_email,
                    duration_minutes, table_id, special_requests, occasion,
                    status, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'confirmed', $13)
                RETURNING id
            """,
                ctx["tenant_id"], res_number, data.reservation_date,
                data.reservation_time, data.party_size, data.customer_name,
                data.customer_phone, data.customer_email, data.duration_minutes,
                table_id, data.special_requests, data.occasion, ctx["user_id"]
            )

            return TableResponse(
                success=True,
                message="Reservation created",
                data={
                    "id": str(row["id"]),
                    "reservation_number": res_number,
                    "table_id": str(table_id) if table_id else None
                }
            )


@router.get("/reservations/{reservation_id}", response_model=ReservationDetailResponse)
async def get_reservation(request: Request, reservation_id: UUID):
    """Get reservation details."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        res = await conn.fetchrow("""
            SELECT r.*, rt.table_number, ta.id as area_id, ta.area_name
            FROM table_reservations r
            LEFT JOIN restaurant_tables rt ON rt.id = r.table_id
            LEFT JOIN table_areas ta ON ta.id = rt.area_id
            WHERE r.id = $1 AND r.tenant_id = $2
        """, reservation_id, ctx["tenant_id"])

        if not res:
            raise HTTPException(status_code=404, detail="Reservation not found")

        end_time = (datetime.combine(date.today(), res["reservation_time"]) +
                   timedelta(minutes=res["duration_minutes"])).time()

        return ReservationDetailResponse(
            success=True,
            id=str(res["id"]),
            reservation_number=res["reservation_number"],
            reservation_date=res["reservation_date"],
            reservation_time=res["reservation_time"],
            end_time=end_time,
            party_size=res["party_size"],
            customer_name=res["customer_name"],
            customer_phone=res["customer_phone"],
            customer_email=res["customer_email"],
            table_id=str(res["table_id"]) if res["table_id"] else None,
            table_number=res["table_number"],
            area_id=str(res["area_id"]) if res["area_id"] else None,
            area_name=res["area_name"],
            status=res["status"],
            duration_minutes=res["duration_minutes"],
            special_requests=res["special_requests"],
            occasion=res["occasion"],
            confirmed_at=res["confirmed_at"],
            cancelled_at=res["cancelled_at"],
            seated_at=res["seated_at"],
            created_at=res["created_at"]
        )


@router.put("/reservations/{reservation_id}", response_model=TableResponse)
async def update_reservation(
    request: Request,
    reservation_id: UUID,
    data: UpdateReservationRequest
):
    """Update a reservation."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM table_reservations WHERE id = $1 AND tenant_id = $2",
            reservation_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Reservation not found")

        updates = []
        params = []
        param_idx = 1

        update_data = data.model_dump(exclude_unset=True)

        # Handle status changes
        if "status" in update_data:
            if update_data["status"] == "cancelled":
                update_data["cancelled_at"] = datetime.now()

        for field, value in update_data.items():
            updates.append(f"{field} = ${param_idx}")
            params.append(value)
            param_idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(reservation_id)
        await conn.execute(f"""
            UPDATE reservations
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
        """, *params)

        return TableResponse(success=True, message="Reservation updated")


@router.post("/reservations/{reservation_id}/seat", response_model=TableResponse)
async def seat_reservation(request: Request, reservation_id: UUID):
    """Mark reservation as seated and create table session."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            res = await conn.fetchrow("""
                SELECT id, table_id, party_size
                FROM table_reservations
                WHERE id = $1 AND tenant_id = $2 AND status = 'confirmed'
            """, reservation_id, ctx["tenant_id"])

            if not res:
                raise HTTPException(status_code=404, detail="Confirmed reservation not found")

            if not res["table_id"]:
                raise HTTPException(status_code=400, detail="No table assigned to reservation")

            # Check table not already occupied
            active_session = await conn.fetchval("""
                SELECT 1 FROM table_sessions
                WHERE table_id = $1 AND status = 'active'
            """, res["table_id"])

            if active_session:
                raise HTTPException(status_code=400, detail="Table is currently occupied")

            # Update reservation
            await conn.execute("""
                UPDATE reservations
                SET status = 'seated', seated_at = NOW()
                WHERE id = $1
            """, reservation_id)

            # Create table session
            session = await conn.fetchrow("""
                INSERT INTO table_sessions (
                    tenant_id, table_id, guest_count, reservation_id, created_by
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """,
                ctx["tenant_id"], res["table_id"], res["party_size"],
                reservation_id, ctx["user_id"]
            )

            return TableResponse(
                success=True,
                message="Reservation seated",
                data={"session_id": str(session["id"])}
            )


# =============================================================================
# TABLE SESSIONS
# =============================================================================

@router.get("/sessions", response_model=TableSessionListResponse)
async def list_table_sessions(
    request: Request,
    status: Optional[str] = None,
    table_id: Optional[UUID] = None
):
    """List table sessions."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["ts.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            if status == 'active':
                where_clauses.append("ts.vacated_at IS NULL")
            elif status == 'closed':
                where_clauses.append("ts.vacated_at IS NOT NULL")
            # Don't add param for status

        if table_id:
            where_clauses.append(f"ts.table_id = ${param_idx}")
            params.append(table_id)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        rows = await conn.fetch(f"""
            SELECT
                ts.id, ts.table_id, rt.table_number, ta.area_name,
                ts.guest_count, u.name as server_name, CASE WHEN ts.vacated_at IS NULL THEN 'active' ELSE 'closed' END as status,
                ts.seated_at, ts.reservation_id,
                EXTRACT(EPOCH FROM (COALESCE(ts.vacated_at, NOW()) - ts.seated_at))::INT / 60 as duration_minutes,
                0 as order_count, 0 as total_amount
            FROM table_sessions ts
            JOIN restaurant_tables rt ON rt.id = ts.table_id
            JOIN table_areas ta ON ta.id = rt.area_id
            LEFT JOIN users u ON u.id = ts.server_id
            WHERE {where_sql}
            ORDER BY ts.seated_at DESC
            LIMIT 100
        """, *params)

        items = [{
            "id": str(r["id"]),
            "table_id": str(r["table_id"]),
            "table_number": r["table_number"],
            "area_name": r["area_name"],
            "guest_count": r["guest_count"],
            "server_name": r["server_name"],
            "status": r["status"],
            "started_at": r["started_at"],
            "duration_minutes": r["duration_minutes"],
            "order_count": r["order_count"],
            "total_amount": r["total_amount"],
            "reservation_id": str(r["reservation_id"]) if r["reservation_id"] else None
        } for r in rows]

        return TableSessionListResponse(items=items, total=len(items))


@router.post("/sessions", response_model=TableResponse)
async def create_table_session(request: Request, data: CreateTableSessionRequest):
    """Create a table session (seat guests)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Check table exists and is available
            table = await conn.fetchrow("""
                SELECT id, is_active FROM restaurant_tables
                WHERE id = $1 AND tenant_id = $2
            """, data.table_id, ctx["tenant_id"])

            if not table:
                raise HTTPException(status_code=404, detail="Table not found")

            if not table["is_active"]:
                raise HTTPException(status_code=400, detail="Table is not active")

            # Check no active session
            active = await conn.fetchval("""
                SELECT 1 FROM table_sessions
                WHERE table_id = $1 AND status = 'active'
            """, data.table_id)

            if active:
                raise HTTPException(status_code=400, detail="Table already has active session")

            row = await conn.fetchrow("""
                INSERT INTO table_sessions (
                    tenant_id, table_id, guest_count, reservation_id,
                    server_id, notes, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """,
                ctx["tenant_id"], data.table_id, data.guest_count,
                data.reservation_id, data.server_id, data.notes, ctx["user_id"]
            )

            return TableResponse(
                success=True,
                message="Table session created",
                data={"id": str(row["id"])}
            )


@router.post("/sessions/{session_id}/close", response_model=TableResponse)
async def close_table_session(request: Request, session_id: UUID):
    """Close a table session."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        updated = await conn.fetchval("""
            UPDATE table_sessions
            SET status = 'closed', closed_at = NOW()
            WHERE id = $1 AND tenant_id = $2 AND status = 'active'
            RETURNING id
        """, session_id, ctx["tenant_id"])

        if not updated:
            raise HTTPException(status_code=404, detail="Active session not found")

        return TableResponse(success=True, message="Session closed")


# =============================================================================
# WAITLIST
# =============================================================================

@router.get("/waitlist", response_model=WaitlistResponse)
async def get_waitlist(request: Request):
    """Get current waitlist."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        rows = await conn.fetch("""
            SELECT
                w.id, w.queue_number, w.customer_name, w.customer_phone,
                w.party_size, ta.name as area_preference, w.seating_preference,
                w.status, w.created_at,
                EXTRACT(EPOCH FROM (NOW() - w.created_at))::INT / 60 as actual_wait_minutes
            FROM waitlist w
            LEFT JOIN table_areas ta ON ta.id = w.area_preference
            WHERE w.tenant_id = $1
            AND DATE(w.created_at) = CURRENT_DATE
            AND w.status IN ('waiting', 'notified')
            ORDER BY w.queue_number
        """, ctx["tenant_id"])

        waiting_count = sum(1 for r in rows if r["status"] == "waiting")
        avg_wait = sum(r["actual_wait_minutes"] for r in rows) / len(rows) if rows else 0

        items = [{
            "id": str(r["id"]),
            "queue_number": r["queue_number"],
            "customer_name": r["customer_name"],
            "customer_phone": r["customer_phone"],
            "party_size": r["party_size"],
            "area_preference": r["area_preference"],
            "seating_preference": r["seating_preference"],
            "status": r["status"],
            "estimated_wait_minutes": 15,  # Simplified estimate
            "actual_wait_minutes": r["actual_wait_minutes"],
            "created_at": r["created_at"]
        } for r in rows]

        return WaitlistResponse(
            items=items,
            total_waiting=waiting_count,
            avg_wait_minutes=int(avg_wait)
        )


@router.post("/waitlist", response_model=TableResponse)
async def add_to_waitlist(request: Request, data: CreateWaitlistEntryRequest):
    """Add guest to waitlist."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        # Get next queue number for today
        max_queue = await conn.fetchval("""
            SELECT COALESCE(MAX(queue_number), 0)
            FROM waitlist
            WHERE tenant_id = $1 AND DATE(created_at) = CURRENT_DATE
        """, ctx["tenant_id"])

        row = await conn.fetchrow("""
            INSERT INTO waitlist (
                tenant_id, queue_number, customer_name, customer_phone,
                party_size, area_preference, seating_preference, notes,
                created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id, queue_number
        """,
            ctx["tenant_id"], max_queue + 1, data.customer_name,
            data.customer_phone, data.party_size, data.area_preference,
            data.seating_preference, data.notes, ctx["user_id"]
        )

        return TableResponse(
            success=True,
            message="Added to waitlist",
            data={
                "id": str(row["id"]),
                "queue_number": row["queue_number"]
            }
        )


@router.post("/waitlist/{entry_id}/seat", response_model=TableResponse)
async def seat_from_waitlist(
    request: Request,
    entry_id: UUID,
    table_id: UUID = Query(...)
):
    """Seat a guest from waitlist."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Get waitlist entry
            entry = await conn.fetchrow("""
                SELECT id, party_size FROM waitlist
                WHERE id = $1 AND tenant_id = $2 AND status = 'waiting'
            """, entry_id, ctx["tenant_id"])

            if not entry:
                raise HTTPException(status_code=404, detail="Waitlist entry not found")

            # Update waitlist
            await conn.execute("""
                UPDATE waitlist
                SET status = 'seated', seated_at = NOW()
                WHERE id = $1
            """, entry_id)

            # Create session
            session = await conn.fetchrow("""
                INSERT INTO table_sessions (
                    tenant_id, table_id, guest_count, created_by
                )
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """,
                ctx["tenant_id"], table_id, entry["party_size"], ctx["user_id"]
            )

            return TableResponse(
                success=True,
                message="Guest seated from waitlist",
                data={"session_id": str(session["id"])}
            )


# =============================================================================
# FLOOR PLAN
# =============================================================================

@router.get("/floor-plan/{area_id}", response_model=FloorPlanResponse)
async def get_floor_plan(request: Request, area_id: UUID):
    """Get floor plan layout for an area."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        area = await conn.fetchrow("""
            SELECT id, name, floor_number FROM table_areas
            WHERE id = $1 AND tenant_id = $2
        """, area_id, ctx["tenant_id"])

        if not area:
            raise HTTPException(status_code=404, detail="Area not found")

        tables = await conn.fetch("""
            SELECT
                rt.id, rt.table_number, rt.max_capacity, rt.shape as table_shape,
                COALESCE(rt.position_x, 0) as position_x,
                COALESCE(rt.position_y, 0) as position_y,
                ts.guest_count as current_guests,
                CASE WHEN ts.id IS NOT NULL
                     THEN EXTRACT(EPOCH FROM (NOW() - ts.seated_at))::INT / 60
                     ELSE NULL END as session_duration_minutes,
                CASE
                    WHEN ts.vacated_at IS NULL THEN 'occupied'
                    WHEN NOT rt.is_active THEN 'blocked'
                    ELSE 'available'
                END as status
            FROM restaurant_tables rt
            LEFT JOIN table_sessions ts ON ts.table_id = rt.id AND ts.vacated_at IS NULL
            WHERE rt.area_id = $1
            ORDER BY rt.table_number
        """, area_id)

        return FloorPlanResponse(
            success=True,
            area_id=str(area["id"]),
            area_name=area["name"],
            floor_number=area["floor_number"],
            tables=[{
                "id": str(t["id"]),
                "table_number": t["table_number"],
                "capacity_max": t["capacity_max"],
                "table_shape": t["table_shape"],
                "position_x": t["position_x"],
                "position_y": t["position_y"],
                "status": t["status"],
                "current_guests": t["current_guests"],
                "session_duration_minutes": t["session_duration_minutes"]
            } for t in tables],
            dimensions={"width": 800, "height": 600}
        )


# =============================================================================
# TABLE AVAILABILITY
# =============================================================================

@router.post("/availability", response_model=TableAvailabilityResponse)
async def check_availability(request: Request, data: TableAvailabilityRequest):
    """Check table availability for a date and party size."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        start_time = data.start_time or time(11, 0)  # Default 11 AM
        end_time = data.end_time or time(22, 0)  # Default 10 PM

        time_slots = []
        current = datetime.combine(data.date, start_time)
        end = datetime.combine(data.date, end_time)

        while current <= end:
            slot_time = current.time()

            # Find available tables for this slot
            available = await conn.fetch("""
                SELECT rt.id FROM restaurant_tables rt
                WHERE rt.tenant_id = $1
                AND rt.is_active = true
                AND true as is_reservable = true
                AND rt.max_capacity >= $2
                AND ($3::UUID IS NULL OR rt.area_id = $3)
                AND NOT EXISTS (
                    SELECT 1 FROM table_reservations r
                    WHERE r.table_id = rt.id
                    AND r.reservation_date = $4
                    AND r.status = 'confirmed'
                    AND (
                        (r.reservation_time <= $5 AND r.reservation_time + (r.duration_minutes || ' minutes')::INTERVAL > $5)
                        OR
                        ($5 <= r.reservation_time AND $5 + ($6 || ' minutes')::INTERVAL > r.reservation_time)
                    )
                )
            """,
                ctx["tenant_id"], data.party_size, data.area_id,
                data.date, slot_time, data.duration_minutes
            )

            time_slots.append({
                "time": slot_time,
                "available_tables": len(available),
                "table_ids": [str(t["id"]) for t in available]
            })

            current += timedelta(minutes=30)

        return TableAvailabilityResponse(
            success=True,
            date=data.date,
            party_size=data.party_size,
            time_slots=time_slots
        )


# =============================================================================
# TABLE STATISTICS
# =============================================================================

@router.get("/stats", response_model=TableStatsResponse)
async def get_table_stats(
    request: Request,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
):
    """Get table turnover and revenue statistics."""
    ctx = get_user_context(request)
    pool = await get_pool()

    if not start_date:
        start_date = date.today() - timedelta(days=7)
    if not end_date:
        end_date = date.today()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        rows = await conn.fetch("""
            SELECT
                rt.id as table_id, rt.table_number, ta.area_name,
                COUNT(ts.id) as sessions_count,
                COALESCE(SUM(ts.guest_count), 0) as total_guests,
                0 as total_revenue,
                COALESCE(AVG(EXTRACT(EPOCH FROM (ts.vacated_at - ts.seated_at)) / 60), 0)::INT as avg_session_minutes
            FROM restaurant_tables rt
            JOIN table_areas ta ON ta.id = rt.area_id
            LEFT JOIN table_sessions ts ON ts.table_id = rt.id
                AND DATE(ts.seated_at) BETWEEN $2 AND $3
                AND ts.vacated_at IS NOT NULL
            WHERE rt.tenant_id = $1
            GROUP BY rt.id, rt.table_number, ta.name
            ORDER BY sessions_count DESC
        """, ctx["tenant_id"], start_date, end_date)

        period_days = (end_date - start_date).days + 1

        tables = []
        for r in rows:
            turnover = Decimal(str(r["sessions_count"])) / Decimal(str(period_days)) if period_days > 0 else Decimal("0")
            tables.append({
                "table_id": str(r["table_id"]),
                "table_number": r["table_number"],
                "area_name": r["area_name"],
                "sessions_count": r["sessions_count"],
                "total_guests": r["total_guests"],
                "total_revenue": r["total_revenue"],
                "avg_session_minutes": r["avg_session_minutes"],
                "turnover_rate": round(turnover, 2)
            })

        total_sessions = sum(t["sessions_count"] for t in tables)
        total_guests = sum(t["total_guests"] for t in tables)

        return TableStatsResponse(
            success=True,
            period_start=start_date,
            period_end=end_date,
            tables=tables,
            summary={
                "total_sessions": total_sessions,
                "total_guests": total_guests,
                "avg_sessions_per_day": total_sessions / period_days if period_days > 0 else 0
            }
        )
