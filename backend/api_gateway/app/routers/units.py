"""
Units Router - Product Units (Satuan) Master Data Management

CRUD endpoints for managing units of measure with conversion support.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging

from ..services.db_pool import get_db_pool

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


# =============================================================================
# LIST UNITS
# =============================================================================
@router.get("")
async def list_units(
    request: Request,
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List all units — system + custom."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2

            if search:
                conditions.append(f"(name ILIKE ${idx} OR abbreviation ILIKE ${idx})")
                params.append(f"%{search}%")
                idx += 1

            if is_active is not None:
                conditions.append(f"is_active = ${idx}")
                params.append(is_active)
                idx += 1

            where = " AND ".join(conditions)
            params.append(limit)

            rows = await conn.fetch(
                f"""
                SELECT id, name, abbreviation, is_system, is_active, created_at
                FROM product_units
                WHERE {where}
                ORDER BY is_system DESC, name ASC
                LIMIT ${idx}
            """,
                *params,
            )

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM product_units WHERE {where}", *params[:-1]
            )

            return {
                "items": [
                    {
                        "id": str(r["id"]),
                        "name": r["name"],
                        "abbreviation": r["abbreviation"],
                        "is_system": r["is_system"],
                        "is_active": r["is_active"],
                    }
                    for r in rows
                ],
                "total": total,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing units: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list units")


# =============================================================================
# DROPDOWN (for pickers)
# =============================================================================
@router.get("/dropdown")
async def units_dropdown(request: Request, search: Optional[str] = Query(None)):
    """Active units for pickers — lightweight."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            if search:
                rows = await conn.fetch(
                    """
                    SELECT id, name, abbreviation
                    FROM product_units
                    WHERE tenant_id = $1 AND is_active = true
                      AND (name ILIKE $2 OR abbreviation ILIKE $2)
                    ORDER BY is_system DESC, name ASC LIMIT 50
                """,
                    ctx["tenant_id"],
                    f"%{search}%",
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, name, abbreviation
                    FROM product_units
                    WHERE tenant_id = $1 AND is_active = true
                    ORDER BY is_system DESC, name ASC LIMIT 100
                """,
                    ctx["tenant_id"],
                )

            return {
                "items": [
                    {
                        "id": str(r["id"]),
                        "name": r["name"],
                        "abbreviation": r["abbreviation"],
                    }
                    for r in rows
                ]
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in units dropdown: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get units")


# =============================================================================
# CREATE UNIT
# =============================================================================
@router.post("", status_code=201)
async def create_unit(request: Request):
    """Create a custom unit."""
    try:
        ctx = get_user_context(request)
        body = await request.json()
        name = (body.get("name") or "").strip()
        abbreviation = (body.get("abbreviation") or "").strip().lower()

        if not name or not abbreviation:
            raise HTTPException(
                status_code=400, detail="Nama dan singkatan wajib diisi"
            )
        if len(name) > 50:
            raise HTTPException(status_code=400, detail="Nama maksimal 50 karakter")
        if len(abbreviation) > 20:
            raise HTTPException(
                status_code=400, detail="Singkatan maksimal 20 karakter"
            )

        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM product_units WHERE tenant_id = $1 AND LOWER(abbreviation) = $2",
                ctx["tenant_id"],
                abbreviation,
            )
            if existing:
                raise HTTPException(
                    status_code=400, detail=f"Satuan '{abbreviation}' sudah ada"
                )

            unit_id = await conn.fetchval(
                """
                INSERT INTO product_units (tenant_id, name, abbreviation, is_system)
                VALUES ($1, $2, $3, false) RETURNING id
            """,
                ctx["tenant_id"],
                name,
                abbreviation,
            )

            return {
                "success": True,
                "message": "Satuan berhasil dibuat",
                "data": {
                    "id": str(unit_id),
                    "name": name,
                    "abbreviation": abbreviation,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating unit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membuat satuan")


# =============================================================================
# UPDATE UNIT
# =============================================================================
@router.patch("/{unit_id}")
async def update_unit(request: Request, unit_id: UUID):
    """Update a custom unit (name/abbreviation)."""
    try:
        ctx = get_user_context(request)
        body = await request.json()
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, is_system, abbreviation FROM product_units WHERE id = $1 AND tenant_id = $2",
                unit_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")

            updates = []
            params = []
            idx = 1

            if "name" in body:
                updates.append(f"name = ${idx}")
                params.append(body["name"].strip())
                idx += 1

            if "abbreviation" in body:
                new_abbr = body["abbreviation"].strip().lower()
                if new_abbr != existing["abbreviation"]:
                    dup = await conn.fetchval(
                        "SELECT id FROM product_units WHERE tenant_id = $1 AND LOWER(abbreviation) = $2 AND id != $3",
                        ctx["tenant_id"],
                        new_abbr,
                        unit_id,
                    )
                    if dup:
                        raise HTTPException(
                            status_code=400, detail=f"Satuan '{new_abbr}' sudah ada"
                        )
                updates.append(f"abbreviation = ${idx}")
                params.append(new_abbr)
                idx += 1

            if not updates:
                return {"success": True, "message": "Tidak ada perubahan"}

            updates.append("updated_at = NOW()")
            params.extend([unit_id, ctx["tenant_id"]])

            await conn.execute(
                f"""
                UPDATE product_units SET {', '.join(updates)}
                WHERE id = ${idx} AND tenant_id = ${idx + 1}
            """,
                *params,
            )

            return {
                "success": True,
                "message": "Satuan berhasil diperbarui",
                "data": {"id": str(unit_id)},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating unit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memperbarui satuan")


# =============================================================================
# DELETE UNIT (soft deactivate, guarded)
# =============================================================================
@router.delete("/{unit_id}")
async def delete_unit(request: Request, unit_id: UUID):
    """Deactivate a unit — guarded against is_system and product references."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, name, abbreviation, is_system FROM product_units WHERE id = $1 AND tenant_id = $2",
                unit_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
            if existing["is_system"]:
                raise HTTPException(
                    status_code=400, detail="Tidak dapat menghapus satuan sistem"
                )

            # Check products using this unit
            product_count = await conn.fetchval(
                "SELECT COUNT(*) FROM products WHERE tenant_id = $1 AND LOWER(satuan) = $2 AND deleted_at IS NULL",
                ctx["tenant_id"],
                existing["abbreviation"],
            )
            if product_count > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Satuan '{existing['name']}' masih digunakan oleh {product_count} produk",
                )

            await conn.execute(
                "UPDATE product_units SET is_active = false, updated_at = NOW() WHERE id = $1",
                unit_id,
            )

            return {
                "success": True,
                "message": "Satuan berhasil dihapus",
                "data": {"id": str(unit_id)},
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting unit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menghapus satuan")


# =============================================================================
# LIST ALL CONVERSIONS (for Satuan desktop page)
# =============================================================================
@router.get("/conversions")
async def list_all_conversions(request: Request):
    """List all unit conversions for the tenant — joined with product info."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT uc.id, uc.product_id,
                       COALESCE(p.nama_produk, p.name, '') AS product_name,
                       COALESCE(p.kode_produk, p.sku, '') AS product_code,
                       uc.base_unit, uc.conversion_unit, uc.conversion_factor,
                       uc.sales_price, uc.purchase_price
                FROM unit_conversions uc
                JOIN products p ON p.id = uc.product_id AND p.tenant_id = uc.tenant_id
                WHERE uc.tenant_id = $1 AND uc.is_active = true
                ORDER BY p.nama_produk ASC, uc.conversion_factor ASC
            """,
                ctx["tenant_id"],
            )

            conversions = [
                {
                    "id": str(r["id"]),
                    "product_id": str(r["product_id"]),
                    "product_name": r["product_name"],
                    "product_code": r["product_code"],
                    "base_unit": r["base_unit"],
                    "conversion_unit": r["conversion_unit"],
                    "conversion_factor": float(r["conversion_factor"]),
                    "sales_price": float(r["sales_price"])
                    if r["sales_price"]
                    else None,
                    "purchase_price": float(r["purchase_price"])
                    if r["purchase_price"]
                    else None,
                }
                for r in rows
            ]

            return {"success": True, "conversions": conversions}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing all conversions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat konversi")


# =============================================================================
# GET CONVERSIONS FOR A PRODUCT
# =============================================================================
@router.get("/conversions/{product_id}")
async def get_conversions(request: Request, product_id: UUID):
    """Get unit conversions for a product with computed chains."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            product = await conn.fetchrow(
                "SELECT id, COALESCE(base_unit, satuan, 'pcs') AS base_unit FROM products WHERE id = $1 AND tenant_id = $2",
                product_id,
                ctx["tenant_id"],
            )
            if not product:
                raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

            rows = await conn.fetch(
                """
                SELECT id, base_unit, conversion_unit, conversion_factor,
                       purchase_price, sales_price, is_active
                FROM unit_conversions
                WHERE product_id = $1 AND tenant_id = $2 AND is_active = true
                ORDER BY conversion_factor ASC
            """,
                product_id,
                ctx["tenant_id"],
            )

            conversions = [
                {
                    "id": str(r["id"]),
                    "from_unit": r["base_unit"],
                    "to_unit": r["conversion_unit"],
                    "factor": int(r["conversion_factor"])
                    if r["conversion_factor"] == int(r["conversion_factor"])
                    else float(r["conversion_factor"]),
                    "purchase_price": int(r["purchase_price"])
                    if r["purchase_price"]
                    else None,
                    "sales_price": int(r["sales_price"]) if r["sales_price"] else None,
                }
                for r in rows
            ]

            # Compute transitive chains to base unit
            base = product["base_unit"]
            chains = []
            # Build factor map: unit -> (factor_to_base_unit)
            factor_map = {base: 1}
            changed = True
            while changed:
                changed = False
                for c in conversions:
                    if c["from_unit"] in factor_map and c["to_unit"] not in factor_map:
                        factor_map[c["to_unit"]] = (
                            factor_map[c["from_unit"]] * c["factor"]
                        )
                        changed = True
                    elif (
                        c["to_unit"] in factor_map and c["from_unit"] not in factor_map
                    ):
                        factor_map[c["from_unit"]] = (
                            factor_map[c["to_unit"]] / c["factor"]
                        )
                        changed = True

            for unit, factor in factor_map.items():
                if unit != base and factor != 1:
                    chains.append({"from": unit, "to": base, "factor": factor})

            return {
                "product_id": str(product_id),
                "base_unit": base,
                "conversions": conversions,
                "computed_chains": sorted(chains, key=lambda x: x["factor"]),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat konversi")


# =============================================================================
# CREATE/UPDATE CONVERSION FOR A PRODUCT
# =============================================================================
@router.post("/conversions/{product_id}", status_code=201)
async def set_conversion(request: Request, product_id: UUID):
    """Add or update a unit conversion for a product."""
    try:
        ctx = get_user_context(request)
        body = await request.json()
        from_unit = (body.get("from_unit") or "").strip().lower()
        to_unit = (body.get("to_unit") or "").strip().lower()
        factor = body.get("factor")
        purchase_price = body.get("purchase_price")
        sales_price = body.get("sales_price")

        if not from_unit or not to_unit or not factor:
            raise HTTPException(
                status_code=400, detail="from_unit, to_unit, dan factor wajib diisi"
            )
        if from_unit == to_unit:
            raise HTTPException(
                status_code=400, detail="Satuan asal dan tujuan tidak boleh sama"
            )
        if factor <= 0:
            raise HTTPException(
                status_code=400, detail="Faktor konversi harus lebih dari 0"
            )

        pool = await get_pool()
        async with pool.acquire() as conn:
            # Verify product exists
            product = await conn.fetchval(
                "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
                product_id,
                ctx["tenant_id"],
            )
            if not product:
                raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

            # Check duplicate
            existing = await conn.fetchrow(
                """
                SELECT id FROM unit_conversions
                WHERE product_id = $1 AND tenant_id = $2
                  AND LOWER(base_unit) = $3 AND LOWER(conversion_unit) = $4
            """,
                product_id,
                ctx["tenant_id"],
                from_unit,
                to_unit,
            )

            if existing:
                # Update existing
                await conn.execute(
                    """
                    UPDATE unit_conversions
                    SET conversion_factor = $1, purchase_price = $2, sales_price = $3, updated_at = NOW()
                    WHERE id = $4
                """,
                    factor,
                    purchase_price,
                    sales_price,
                    existing["id"],
                )
                return {
                    "success": True,
                    "message": "Konversi berhasil diperbarui",
                    "data": {"id": str(existing["id"])},
                }
            else:
                # Create new
                conv_id = await conn.fetchval(
                    """
                    INSERT INTO unit_conversions (tenant_id, product_id, base_unit, conversion_unit, conversion_factor, purchase_price, sales_price)
                    VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                """,
                    ctx["tenant_id"],
                    product_id,
                    from_unit,
                    to_unit,
                    factor,
                    purchase_price,
                    sales_price,
                )
                return {
                    "success": True,
                    "message": "Konversi berhasil ditambahkan",
                    "data": {"id": str(conv_id)},
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting conversion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menyimpan konversi")


# =============================================================================
# DELETE CONVERSION
# =============================================================================
@router.delete("/conversions/{conversion_id}")
async def delete_conversion(request: Request, conversion_id: UUID):
    """Remove a unit conversion."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM unit_conversions WHERE id = $1 AND tenant_id = $2",
                conversion_id,
                ctx["tenant_id"],
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Konversi tidak ditemukan")

            await conn.execute(
                "UPDATE unit_conversions SET is_active = false, updated_at = NOW() WHERE id = $1",
                conversion_id,
            )

            return {"success": True, "message": "Konversi berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting conversion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menghapus konversi")
