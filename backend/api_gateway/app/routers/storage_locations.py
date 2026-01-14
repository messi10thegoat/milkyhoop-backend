"""
Storage Locations Router - Lokasi Penyimpanan Management
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.storage_locations import (
    CreateStorageLocationRequest,
    UpdateStorageLocationRequest,
    StorageLocationResponse,
    StorageLocationListResponse,
    StorageLocationDetailResponse,
    StorageLocationTreeResponse,
    StorageLocationDropdownResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10, command_timeout=30)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": UUID(user.get("user_id")) if user.get("user_id") else None}


def build_tree(locations: list, parent_id=None) -> list:
    tree = []
    for loc in locations:
        if loc.get("parent_id") == parent_id:
            children = build_tree(locations, loc["id"])
            tree.append({
                "id": loc["id"], "code": loc["code"], "name": loc["name"],
                "location_type": loc["location_type"], "is_active": loc["is_active"],
                "children": children
            })
    return tree


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "storage-locations"}


@router.get("/dropdown", response_model=StorageLocationDropdownResponse)
async def get_dropdown(request: Request, location_type: Optional[str] = None):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1", "is_active = true"]
            params = [ctx["tenant_id"]]
            if location_type:
                conditions.append("location_type = $2")
                params.append(location_type)
            query = f"SELECT id, code, name, location_type FROM storage_locations WHERE {' AND '.join(conditions)} ORDER BY code"
            rows = await conn.fetch(query, *params)
            return {"items": [{"id": str(r["id"]), "code": r["code"], "name": r["name"],
                             "location_type": r["location_type"], "full_name": f"{r['code']} - {r['name']}"} for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.get("/tree", response_model=StorageLocationTreeResponse)
async def get_tree(request: Request):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, code, name, location_type, parent_id, is_active FROM storage_locations WHERE tenant_id = $1 ORDER BY code",
                ctx["tenant_id"]
            )
            locations = [{"id": str(r["id"]), "code": r["code"], "name": r["name"],
                         "location_type": r["location_type"], "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
                         "is_active": r["is_active"]} for r in rows]
            return {"items": build_tree(locations)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.get("", response_model=StorageLocationListResponse)
async def list_locations(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    location_type: Optional[str] = None,
    is_active: Optional[bool] = None
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2
            if search:
                conditions.append(f"(code ILIKE ${idx} OR name ILIKE ${idx})")
                params.append(f"%{search}%")
                idx += 1
            if location_type:
                conditions.append(f"location_type = ${idx}")
                params.append(location_type)
                idx += 1
            if is_active is not None:
                conditions.append(f"is_active = ${idx}")
                params.append(is_active)
                idx += 1
            where = " AND ".join(conditions)
            total = await conn.fetchval(f"SELECT COUNT(*) FROM storage_locations WHERE {where}", *params)
            params.extend([limit, skip])
            rows = await conn.fetch(f"""
                SELECT id, code, name, location_type, parent_id, is_active, is_default
                FROM storage_locations WHERE {where} ORDER BY code LIMIT ${idx} OFFSET ${idx+1}
            """, *params)
            return {"items": [{"id": str(r["id"]), "code": r["code"], "name": r["name"],
                             "location_type": r["location_type"], "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
                             "is_active": r["is_active"], "is_default": r["is_default"]} for r in rows],
                    "total": total, "has_more": skip + limit < total}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.get("/{location_id}", response_model=StorageLocationDetailResponse)
async def get_location(request: Request, location_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT l.*, p.name as parent_name FROM storage_locations l
                LEFT JOIN storage_locations p ON l.parent_id = p.id
                WHERE l.id = $1 AND l.tenant_id = $2
            """, location_id, ctx["tenant_id"])
            if not row:
                raise HTTPException(status_code=404, detail="Location not found")
            return {"success": True, "data": {
                "id": str(row["id"]), "code": row["code"], "name": row["name"],
                "location_type": row["location_type"],
                "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
                "parent_name": row["parent_name"], "address": row["address"],
                "capacity_info": row["capacity_info"], "temperature_range": row["temperature_range"],
                "description": row["description"], "is_active": row["is_active"],
                "is_default": row["is_default"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat()
            }}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.post("", response_model=StorageLocationResponse, status_code=201)
async def create_location(request: Request, body: CreateStorageLocationRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM storage_locations WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], body.code
            )
            if existing:
                raise HTTPException(status_code=400, detail=f"Code '{body.code}' already exists")
            parent_uuid = UUID(body.parent_id) if body.parent_id else None
            loc_id = await conn.fetchval("""
                INSERT INTO storage_locations (tenant_id, code, name, parent_id, location_type,
                    address, capacity_info, temperature_range, description, is_default, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id
            """, ctx["tenant_id"], body.code, body.name, parent_uuid, body.location_type,
                body.address, body.capacity_info, body.temperature_range, body.description,
                body.is_default, ctx["user_id"])
            return {"success": True, "message": "Location created", "data": {"id": str(loc_id), "code": body.code}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.patch("/{location_id}", response_model=StorageLocationResponse)
async def update_location(request: Request, location_id: UUID, body: UpdateStorageLocationRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, code FROM storage_locations WHERE id = $1 AND tenant_id = $2",
                location_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Location not found")
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {"success": True, "message": "No changes", "data": {"id": str(location_id)}}
            if "parent_id" in update_data:
                update_data["parent_id"] = UUID(update_data["parent_id"]) if update_data["parent_id"] else None
            updates, params, idx = [], [], 1
            for field, value in update_data.items():
                updates.append(f"{field} = ${idx}")
                params.append(value)
                idx += 1
            updates.append("updated_at = NOW()")
            params.extend([location_id, ctx["tenant_id"]])
            await conn.execute(f"UPDATE storage_locations SET {', '.join(updates)} WHERE id = ${idx} AND tenant_id = ${idx+1}", *params)
            return {"success": True, "message": "Location updated", "data": {"id": str(location_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.delete("/{location_id}", response_model=StorageLocationResponse)
async def delete_location(request: Request, location_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, code FROM storage_locations WHERE id = $1 AND tenant_id = $2",
                location_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Location not found")
            has_children = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM storage_locations WHERE parent_id = $1)", location_id)
            if has_children:
                raise HTTPException(status_code=400, detail="Cannot delete location with children")
            await conn.execute(
                "UPDATE storage_locations SET is_active = false, updated_at = NOW() WHERE id = $1",
                location_id
            )
            return {"success": True, "message": "Location deleted", "data": {"id": str(location_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")
