"""
Matrix Items Router - Parent-Variant Item Management
"""
from typing import Optional, List, Dict
import json

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings

router = APIRouter()

_pool = None


async def get_pool():
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


@router.get("")
async def list_matrix_items(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List matrix parent items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch("""
            SELECT 
                p.id, p.nama_produk as name, p.base_unit, p.sales_price, p.purchase_price,
                p.image_url, p.kategori, p.status, p.created_at,
                COUNT(v.id) as variant_count
            FROM products p
            LEFT JOIN products v ON v.matrix_parent_id = p.id AND v.deleted_at IS NULL
            WHERE p.tenant_id = $1 
              AND p.is_matrix_parent = true 
              AND p.deleted_at IS NULL
            GROUP BY p.id
            ORDER BY p.nama_produk
            LIMIT $2 OFFSET $3
        """, ctx["tenant_id"], limit, offset)

        total = await conn.fetchval("""
            SELECT COUNT(*) FROM products 
            WHERE tenant_id = $1 AND is_matrix_parent = true AND deleted_at IS NULL
        """, ctx["tenant_id"])

        items = []
        for row in rows:
            items.append({
                "id": str(row["id"]),
                "name": row["name"],
                "base_unit": row["base_unit"],
                "sales_price": float(row["sales_price"]) if row["sales_price"] else None,
                "variant_count": row["variant_count"]
            })

        return {"success": True, "items": items, "total": total}


@router.get("/{item_id}")
async def get_matrix_item(request: Request, item_id: str):
    """Get matrix parent with variants"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        parent = await conn.fetchrow("""
            SELECT * FROM products
            WHERE tenant_id = $1 AND id = $2::uuid 
              AND is_matrix_parent = true AND deleted_at IS NULL
        """, ctx["tenant_id"], item_id)

        if not parent:
            raise HTTPException(status_code=404, detail="Matrix item not found")

        variants = await conn.fetch("""
            SELECT * FROM get_matrix_variants($1, $2::uuid)
        """, ctx["tenant_id"], item_id)

        attribute_names = set()
        variant_list = []
        for v in variants:
            attrs = v["attributes"] if isinstance(v["attributes"], dict) else json.loads(v["attributes"] or "{}")
            attribute_names.update(attrs.keys())
            variant_list.append({
                "id": str(v["variant_id"]),
                "name": v["variant_name"],
                "attributes": attrs,
                "sales_price": float(v["sales_price"]) if v["sales_price"] else None,
                "status": v["status"]
            })

        return {
            "success": True,
            "data": {
                "id": str(parent["id"]),
                "name": parent["nama_produk"],
                "base_unit": parent["base_unit"],
                "attribute_names": list(attribute_names),
                "variants": variant_list
            }
        }


@router.post("")
async def create_matrix_item(request: Request):
    """Create matrix parent item"""
    ctx = get_user_context(request)
    body = await request.json()

    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    if not body.get("base_unit"):
        raise HTTPException(status_code=400, detail="base_unit is required")

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow("""
            INSERT INTO products (
                tenant_id, nama_produk, satuan, base_unit, item_type,
                is_matrix_parent, track_inventory,
                sales_price, purchase_price,
                kategori, status
            ) VALUES (
                $1, $2, $3, $3, 'goods',
                true, false,
                $4, $5,
                $6, 'active'
            )
            RETURNING id
        """,
            ctx["tenant_id"],
            body["name"],
            body["base_unit"],
            body.get("sales_price"),
            body.get("purchase_price"),
            body.get("kategori")
        )

        return {"success": True, "message": "Matrix parent created", "data": {"id": str(row["id"])}}


@router.post("/{item_id}/variants")
async def add_variants(request: Request, item_id: str):
    """Add variants to matrix parent"""
    ctx = get_user_context(request)
    body = await request.json()

    variants = body.get("variants", [])
    if not variants:
        raise HTTPException(status_code=400, detail="variants array is required")

    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        parent = await conn.fetchrow("""
            SELECT id, base_unit, item_type, track_inventory, sales_price, purchase_price
            FROM products
            WHERE tenant_id = $1 AND id = $2::uuid 
              AND is_matrix_parent = true AND deleted_at IS NULL
        """, ctx["tenant_id"], item_id)

        if not parent:
            raise HTTPException(status_code=404, detail="Matrix parent not found")

        created_ids = []
        for v in variants:
            if not v.get("name"):
                continue

            attrs = {}
            for attr in v.get("attributes", []):
                if attr.get("name") and attr.get("value"):
                    attrs[attr["name"]] = attr["value"]

            row = await conn.fetchrow("""
                INSERT INTO products (
                    tenant_id, nama_produk, satuan, base_unit, item_type,
                    matrix_parent_id, matrix_attributes,
                    track_inventory, sales_price, purchase_price,
                    barcode, sku, status
                ) VALUES (
                    $1, $2, $3, $3, $4,
                    $5::uuid, $6::jsonb,
                    $7, $8, $9,
                    $10, $11, 'active'
                )
                RETURNING id
            """,
                ctx["tenant_id"],
                v["name"],
                parent["base_unit"],
                parent["item_type"] or "goods",
                item_id,
                json.dumps(attrs),
                parent["track_inventory"] if parent["track_inventory"] is not None else True,
                v.get("sales_price") or parent["sales_price"],
                v.get("purchase_price") or parent["purchase_price"],
                v.get("barcode"),
                v.get("sku")
            )
            created_ids.append(str(row["id"]))

        return {"success": True, "message": f"Created {len(created_ids)} variants", "data": {"variant_ids": created_ids}}
