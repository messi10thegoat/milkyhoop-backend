"""
Price Lists Router - Daftar Harga Management
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.price_lists import (
    CreatePriceListRequest,
    UpdatePriceListRequest,
    PriceListItemCreate,
    PriceListResponse,
    PriceListListResponse,
    PriceListDetailResponse,
    PriceListDropdownResponse,
    ItemPriceRequest,
    ItemPriceResponse,
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


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "price-lists"}


@router.get("/dropdown", response_model=PriceListDropdownResponse)
async def get_dropdown(request: Request):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, code, name, is_default FROM price_lists WHERE tenant_id = $1 AND is_active = true ORDER BY priority, name",
                ctx["tenant_id"]
            )
            return {"items": [{"id": str(r["id"]), "code": r["code"], "name": r["name"],
                             "is_default": r["is_default"]} for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.post("/get-price", response_model=ItemPriceResponse)
async def get_item_price(request: Request, body: ItemPriceRequest):
    """Get price for an item based on customer and quantity."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            customer_uuid = UUID(body.customer_id) if body.customer_id else None
            price = await conn.fetchval(
                "SELECT get_item_price($1, $2::uuid, $3::uuid, $4, $5)",
                ctx["tenant_id"], UUID(body.item_id), customer_uuid, body.quantity, body.unit
            )
            return {"success": True, "data": {"item_id": body.item_id, "price": price, "quantity": body.quantity}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.get("", response_model=PriceListListResponse)
async def list_price_lists(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    is_active: Optional[bool] = None
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["p.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2
            if search:
                conditions.append(f"(p.code ILIKE ${idx} OR p.name ILIKE ${idx})")
                params.append(f"%{search}%")
                idx += 1
            if is_active is not None:
                conditions.append(f"p.is_active = ${idx}")
                params.append(is_active)
                idx += 1
            where = " AND ".join(conditions)
            total = await conn.fetchval(f"SELECT COUNT(*) FROM price_lists p WHERE {where}", *params)
            params.extend([limit, skip])
            rows = await conn.fetch(f"""
                SELECT p.id, p.code, p.name, p.price_type, p.start_date, p.end_date,
                       p.is_active, p.is_default,
                       (SELECT COUNT(*) FROM price_list_items WHERE price_list_id = p.id) as item_count
                FROM price_lists p WHERE {where} ORDER BY p.priority, p.name LIMIT ${idx} OFFSET ${idx+1}
            """, *params)
            return {"items": [{"id": str(r["id"]), "code": r["code"], "name": r["name"],
                             "price_type": r["price_type"],
                             "start_date": r["start_date"].isoformat() if r["start_date"] else None,
                             "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                             "is_active": r["is_active"], "is_default": r["is_default"],
                             "item_count": r["item_count"]} for r in rows],
                    "total": total, "has_more": skip + limit < total}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.get("/{price_list_id}", response_model=PriceListDetailResponse)
async def get_price_list(request: Request, price_list_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id, ctx["tenant_id"]
            )
            if not row:
                raise HTTPException(status_code=404, detail="Price list not found")
            items = await conn.fetch(
                "SELECT * FROM price_list_items WHERE price_list_id = $1 ORDER BY item_code",
                price_list_id
            )
            return {"success": True, "data": {
                "id": str(row["id"]), "code": row["code"], "name": row["name"],
                "price_type": row["price_type"],
                "default_discount": float(row["default_discount"] or 0),
                "default_markup": float(row["default_markup"] or 0),
                "start_date": row["start_date"].isoformat() if row["start_date"] else None,
                "end_date": row["end_date"].isoformat() if row["end_date"] else None,
                "priority": row["priority"], "description": row["description"],
                "is_active": row["is_active"], "is_default": row["is_default"],
                "items": [{"id": str(i["id"]), "item_id": str(i["item_id"]),
                          "item_code": i["item_code"], "unit": i["unit"],
                          "price": i["price"], "min_quantity": float(i["min_quantity"]),
                          "discount_percent": float(i["discount_percent"]) if i["discount_percent"] else None,
                          "start_date": i["start_date"].isoformat() if i["start_date"] else None,
                          "end_date": i["end_date"].isoformat() if i["end_date"] else None,
                          "is_active": i["is_active"]} for i in items],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat()
            }}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.post("", response_model=PriceListResponse, status_code=201)
async def create_price_list(request: Request, body: CreatePriceListRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM price_lists WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], body.code
            )
            if existing:
                raise HTTPException(status_code=400, detail=f"Code '{body.code}' already exists")
            async with conn.transaction():
                pl_id = await conn.fetchval("""
                    INSERT INTO price_lists (tenant_id, code, name, price_type,
                        default_discount, default_markup, start_date, end_date,
                        priority, description, is_default, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING id
                """, ctx["tenant_id"], body.code, body.name, body.price_type,
                    body.default_discount, body.default_markup, body.start_date, body.end_date,
                    body.priority, body.description, body.is_default, ctx["user_id"])
                if body.items:
                    for item in body.items:
                        await conn.execute("""
                            INSERT INTO price_list_items (price_list_id, item_id, item_code, unit,
                                price, min_quantity, discount_percent, start_date, end_date)
                            VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9)
                        """, pl_id, item.item_id, item.item_code, item.unit,
                            item.price, item.min_quantity, item.discount_percent,
                            item.start_date, item.end_date)
            return {"success": True, "message": "Price list created", "data": {"id": str(pl_id), "code": body.code}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.patch("/{price_list_id}", response_model=PriceListResponse)
async def update_price_list(request: Request, price_list_id: UUID, body: UpdatePriceListRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, code FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Price list not found")
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {"success": True, "message": "No changes", "data": {"id": str(price_list_id)}}
            updates, params, idx = [], [], 1
            for field, value in update_data.items():
                updates.append(f"{field} = ${idx}")
                params.append(value)
                idx += 1
            updates.append("updated_at = NOW()")
            params.extend([price_list_id, ctx["tenant_id"]])
            await conn.execute(f"UPDATE price_lists SET {', '.join(updates)} WHERE id = ${idx} AND tenant_id = ${idx+1}", *params)
            return {"success": True, "message": "Price list updated", "data": {"id": str(price_list_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.delete("/{price_list_id}", response_model=PriceListResponse)
async def delete_price_list(request: Request, price_list_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, code FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Price list not found")
            await conn.execute(
                "UPDATE price_lists SET is_active = false, is_default = false, updated_at = NOW() WHERE id = $1",
                price_list_id
            )
            return {"success": True, "message": "Price list deleted", "data": {"id": str(price_list_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.post("/{price_list_id}/items", response_model=PriceListResponse)
async def add_item_to_price_list(request: Request, price_list_id: UUID, body: PriceListItemCreate):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id, ctx["tenant_id"]
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Price list not found")
            item_id = await conn.fetchval("""
                INSERT INTO price_list_items (price_list_id, item_id, item_code, unit,
                    price, min_quantity, discount_percent, start_date, end_date)
                VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9) RETURNING id
            """, price_list_id, body.item_id, body.item_code, body.unit,
                body.price, body.min_quantity, body.discount_percent, body.start_date, body.end_date)
            return {"success": True, "message": "Item added", "data": {"id": str(item_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")


@router.delete("/{price_list_id}/items/{item_id}", response_model=PriceListResponse)
async def remove_item_from_price_list(request: Request, price_list_id: UUID, item_id: UUID):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            pl = await conn.fetchrow(
                "SELECT id FROM price_lists WHERE id = $1 AND tenant_id = $2",
                price_list_id, ctx["tenant_id"]
            )
            if not pl:
                raise HTTPException(status_code=404, detail="Price list not found")
            await conn.execute(
                "DELETE FROM price_list_items WHERE id = $1 AND price_list_id = $2",
                item_id, price_list_id
            )
            return {"success": True, "message": "Item removed", "data": {"id": str(item_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed")
