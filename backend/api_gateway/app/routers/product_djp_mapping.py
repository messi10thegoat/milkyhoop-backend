"""
Product DJP Mapping Router — CRUD for product-to-DJP code mapping.

Maps tenant products to DJP kode barang/jasa and satuan ukur,
required for e-Faktur / Coretax export.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
import logging
import asyncpg

from ..schemas.product_djp_mapping import (
    ProductDJPMappingCreate, ProductDJPMappingBulk,
    ProductDJPMappingResponse, ProductDJPMappingListResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    if not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": user.get("tenant_id"), "user_id": user.get("user_id")}


async def get_unmapped_count(conn, tenant_id: str) -> int:
    """Count products without DJP mapping."""
    return await conn.fetchval(
        """SELECT COUNT(*) FROM products p
           WHERE p.tenant_id = $1
           AND NOT EXISTS (
               SELECT 1 FROM product_djp_mapping pdm
               WHERE pdm.product_id = p.id AND pdm.tenant_id = p.tenant_id
           )""",
        tenant_id
    )


async def fetch_enriched_mapping(conn, mapping_id: str, tenant_id: str) -> Optional[dict]:
    """Fetch a single mapping with JOINed product + DJP data."""
    row = await conn.fetchrow(
        """SELECT
               pdm.id, pdm.product_id,
               p.nama_produk AS product_name,
               pdm.djp_kode_barang_jasa_id,
               dkbj.kode AS kode_barang_jasa,
               dkbj.nama AS nama_barang_jasa,
               dkbj.jenis,
               pdm.djp_satuan_ukur_id,
               dsu.kode AS kode_satuan,
               dsu.nama AS nama_satuan
           FROM product_djp_mapping pdm
           JOIN products p ON p.id = pdm.product_id AND p.tenant_id = pdm.tenant_id
           JOIN djp_kode_barang_jasa dkbj ON dkbj.id = pdm.djp_kode_barang_jasa_id
           JOIN djp_satuan_ukur dsu ON dsu.id = pdm.djp_satuan_ukur_id
           WHERE pdm.id = $1 AND pdm.tenant_id = $2""",
        mapping_id, tenant_id
    )
    if row is None:
        return None
    r = dict(row)
    for k in ("id", "product_id", "djp_kode_barang_jasa_id", "djp_satuan_ukur_id"):
        r[k] = str(r[k])
    return r


@router.get("", response_model=ProductDJPMappingListResponse)
async def list_product_djp_mappings(
    request: Request,
    search: Optional[str] = Query(None, description="Search by product name"),
    unmapped_only: bool = Query(False, description="Show only unmapped products"),
):
    """List product DJP mappings, optionally filtered."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        unmapped_count = await get_unmapped_count(conn, ctx["tenant_id"])

        if unmapped_only:
            # Return products WITHOUT mapping
            if search:
                rows = await conn.fetch(
                    """SELECT p.id AS product_id, p.nama_produk AS product_name
                       FROM products p
                       WHERE p.tenant_id = $1
                       AND p.nama_produk ILIKE $2
                       AND NOT EXISTS (
                           SELECT 1 FROM product_djp_mapping pdm
                           WHERE pdm.product_id = p.id AND pdm.tenant_id = p.tenant_id
                       )
                       ORDER BY p.nama_produk""",
                    ctx["tenant_id"], f"%{search}%"
                )
            else:
                rows = await conn.fetch(
                    """SELECT p.id AS product_id, p.nama_produk AS product_name
                       FROM products p
                       WHERE p.tenant_id = $1
                       AND NOT EXISTS (
                           SELECT 1 FROM product_djp_mapping pdm
                           WHERE pdm.product_id = p.id AND pdm.tenant_id = p.tenant_id
                       )
                       ORDER BY p.nama_produk""",
                    ctx["tenant_id"]
                )

            data = [{
                "id": "",
                "product_id": str(r["product_id"]),
                "product_name": r["product_name"],
                "djp_kode_barang_jasa_id": "",
                "kode_barang_jasa": "",
                "nama_barang_jasa": "",
                "jenis": "",
                "djp_satuan_ukur_id": "",
                "kode_satuan": "",
                "nama_satuan": "",
            } for r in rows]

            return {"data": data, "unmapped_count": unmapped_count, "total": len(data)}

        # Default: return mapped products with enriched data
        conditions = ["pdm.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        idx = 2

        if search:
            conditions.append(f"p.nama_produk ILIKE ${idx}")
            params.append(f"%{search}%")
            idx += 1

        where = " AND ".join(conditions)

        rows = await conn.fetch(
            f"""SELECT
                    pdm.id, pdm.product_id,
                    p.nama_produk AS product_name,
                    pdm.djp_kode_barang_jasa_id,
                    dkbj.kode AS kode_barang_jasa,
                    dkbj.nama AS nama_barang_jasa,
                    dkbj.jenis,
                    pdm.djp_satuan_ukur_id,
                    dsu.kode AS kode_satuan,
                    dsu.nama AS nama_satuan
                FROM product_djp_mapping pdm
                JOIN products p ON p.id = pdm.product_id AND p.tenant_id = pdm.tenant_id
                JOIN djp_kode_barang_jasa dkbj ON dkbj.id = pdm.djp_kode_barang_jasa_id
                JOIN djp_satuan_ukur dsu ON dsu.id = pdm.djp_satuan_ukur_id
                WHERE {where}
                ORDER BY p.nama_produk""",
            *params
        )

        data = []
        for r in rows:
            d = dict(r)
            for k in ("id", "product_id", "djp_kode_barang_jasa_id", "djp_satuan_ukur_id"):
                d[k] = str(d[k])
            data.append(d)

        return {"data": data, "unmapped_count": unmapped_count, "total": len(data)}


@router.post("", response_model=ProductDJPMappingResponse, status_code=201)
async def create_product_djp_mapping(request: Request, payload: ProductDJPMappingCreate):
    """Create or update (upsert) a product DJP mapping."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Validate product exists for tenant
        product_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM products WHERE id = $1 AND tenant_id = $2)",
            payload.product_id, ctx["tenant_id"]
        )
        if not product_exists:
            raise HTTPException(status_code=404, detail=f"Product dengan ID {payload.product_id} tidak ditemukan")

        # Validate DJP kode barang/jasa exists
        kode_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM djp_kode_barang_jasa WHERE id = $1)",
            payload.djp_kode_barang_jasa_id
        )
        if not kode_exists:
            raise HTTPException(status_code=400, detail=f"Kode barang/jasa dengan ID {payload.djp_kode_barang_jasa_id} tidak ditemukan")

        # Validate DJP satuan ukur exists
        satuan_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM djp_satuan_ukur WHERE id = $1)",
            payload.djp_satuan_ukur_id
        )
        if not satuan_exists:
            raise HTTPException(status_code=400, detail=f"Satuan ukur dengan ID {payload.djp_satuan_ukur_id} tidak ditemukan")

        # Upsert
        row = await conn.fetchrow(
            """INSERT INTO product_djp_mapping
                   (tenant_id, product_id, djp_kode_barang_jasa_id, djp_satuan_ukur_id)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (tenant_id, product_id)
               DO UPDATE SET
                   djp_kode_barang_jasa_id = EXCLUDED.djp_kode_barang_jasa_id,
                   djp_satuan_ukur_id = EXCLUDED.djp_satuan_ukur_id,
                   updated_at = now()
               RETURNING id""",
            ctx["tenant_id"],
            payload.product_id,
            payload.djp_kode_barang_jasa_id,
            payload.djp_satuan_ukur_id,
        )

        mapping_id = str(row["id"])
        result = await fetch_enriched_mapping(conn, mapping_id, ctx["tenant_id"])
        if result is None:
            raise HTTPException(status_code=500, detail="Mapping created but failed to fetch enriched data")
        return result


@router.post("/bulk")
async def bulk_create_product_djp_mapping(request: Request, payload: ProductDJPMappingBulk):
    """Bulk upsert product DJP mappings in a single transaction."""
    ctx = get_user_context(request)
    pool = await get_pool()

    if not payload.mappings:
        raise HTTPException(status_code=400, detail="mappings tidak boleh kosong")

    created = 0
    updated = 0
    errors = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            for i, m in enumerate(payload.mappings):
                try:
                    # Validate product
                    product_exists = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM products WHERE id = $1 AND tenant_id = $2)",
                        m.product_id, ctx["tenant_id"]
                    )
                    if not product_exists:
                        errors.append({"index": i, "product_id": m.product_id, "error": "Product tidak ditemukan"})
                        continue

                    # Validate DJP refs
                    kode_exists = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM djp_kode_barang_jasa WHERE id = $1)",
                        m.djp_kode_barang_jasa_id
                    )
                    if not kode_exists:
                        errors.append({"index": i, "product_id": m.product_id, "error": "Kode barang/jasa tidak ditemukan"})
                        continue

                    satuan_exists = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM djp_satuan_ukur WHERE id = $1)",
                        m.djp_satuan_ukur_id
                    )
                    if not satuan_exists:
                        errors.append({"index": i, "product_id": m.product_id, "error": "Satuan ukur tidak ditemukan"})
                        continue

                    # Check if mapping already exists
                    existing = await conn.fetchval(
                        "SELECT id FROM product_djp_mapping WHERE tenant_id = $1 AND product_id = $2",
                        ctx["tenant_id"], m.product_id
                    )

                    await conn.execute(
                        """INSERT INTO product_djp_mapping
                               (tenant_id, product_id, djp_kode_barang_jasa_id, djp_satuan_ukur_id)
                           VALUES ($1, $2, $3, $4)
                           ON CONFLICT (tenant_id, product_id)
                           DO UPDATE SET
                               djp_kode_barang_jasa_id = EXCLUDED.djp_kode_barang_jasa_id,
                               djp_satuan_ukur_id = EXCLUDED.djp_satuan_ukur_id,
                               updated_at = now()""",
                        ctx["tenant_id"],
                        m.product_id,
                        m.djp_kode_barang_jasa_id,
                        m.djp_satuan_ukur_id,
                    )

                    if existing:
                        updated += 1
                    else:
                        created += 1

                except Exception as e:
                    errors.append({"index": i, "product_id": m.product_id, "error": str(e)})

    return {"created": created, "updated": updated, "errors": errors}


@router.delete("/{mapping_id}")
async def delete_product_djp_mapping(request: Request, mapping_id: str):
    """Delete a product DJP mapping (hard delete)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM product_djp_mapping WHERE id = $1 AND tenant_id = $2",
            mapping_id, ctx["tenant_id"]
        )

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Mapping tidak ditemukan")

        return {"success": True}
