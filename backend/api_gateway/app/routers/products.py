"""
Products Router - Autocomplete & Search
Source: ItemTransaksi (products that were actually transacted)
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter()


class ProductSuggestion(BaseModel):
    name: str
    unit: str
    last_price: Optional[int] = None
    usage_count: int


class ProductSearchResponse(BaseModel):
    suggestions: List[ProductSuggestion]


class ProductItem(BaseModel):
    name: str
    unit: str
    last_price: Optional[int] = None


class BarcodeProduct(BaseModel):
    """Product details returned from barcode lookup"""
    id: str
    name: str
    unit: str
    category: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    barcode: str


@router.get("/barcode/{barcode}")
async def get_product_by_barcode(
    request: Request,
    barcode: str
):
    """
    Lookup product by barcode (EAN-13, UPC, etc.)

    Used for POS scanning functionality.
    Returns product details if found.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Connect to database
        conn = await asyncpg.connect(
            host="db.ltrqrejrkbusvmknpnwb.supabase.co",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="postgres"
        )

        try:
            # Query product by barcode for this tenant
            query = """
                SELECT
                    id,
                    nama_produk as name,
                    satuan as unit,
                    kategori as category,
                    harga_jual as price,
                    deskripsi as description,
                    barcode
                FROM public.products
                WHERE tenant_id = $1 AND barcode = $2
                LIMIT 1
            """

            row = await conn.fetchrow(query, tenant_id, barcode)

            if not row:
                logger.info(f"Barcode not found: barcode={barcode}, tenant={tenant_id}")
                raise HTTPException(
                    status_code=404,
                    detail=f"Product with barcode '{barcode}' not found"
                )

            result = BarcodeProduct(
                id=str(row['id']),
                name=row['name'],
                unit=row['unit'] or 'pcs',
                category=row['category'],
                price=float(row['price']) if row['price'] else None,
                description=row['description'],
                barcode=row['barcode']
            )

            logger.info(f"Barcode lookup: barcode={barcode}, tenant={tenant_id}, found={result.name}")

            return result

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Barcode lookup error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Barcode lookup failed")


@router.get("/all")
async def get_all_products(
    request: Request,
    limit: int = Query(1000, ge=1, le=2000)
):
    """
    Fetch ALL products for client-side filtering (instant autocomplete).
    Returns products from item_transaksi ordered by usage frequency.

    This endpoint is designed for prefetching - frontend loads all products
    once on mount, then filters locally with Fuse.js for instant results.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Connect to database
        conn = await asyncpg.connect(
            host="db.ltrqrejrkbusvmknpnwb.supabase.co",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="postgres"
        )

        try:
            # Query ALL products from item_transaksi with usage count
            query = """
                SELECT
                    it.nama_produk as name,
                    it.satuan as unit,
                    MAX(it.harga_satuan) as last_price,
                    COUNT(*) as usage_count
                FROM public.item_transaksi it
                JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                WHERE th.tenant_id = $1
                GROUP BY it.nama_produk, it.satuan
                ORDER BY usage_count DESC, it.nama_produk ASC
                LIMIT $2
            """

            rows = await conn.fetch(query, tenant_id, limit)

            results = [
                {
                    "name": row['name'],
                    "unit": row['unit'] or 'pcs',
                    "last_price": int(row['last_price']) if row['last_price'] else None
                }
                for row in rows
            ]

            logger.info(f"Products /all: tenant={tenant_id}, returned={len(results)}")

            return results

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Products /all error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.get("/search", response_model=ProductSearchResponse)
async def search_products(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Search products by name (autocomplete)

    Source: ItemTransaksi (products that were actually transacted)
    Returns products ordered by usage frequency
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Connect to database
        conn = await asyncpg.connect(
            host="db.ltrqrejrkbusvmknpnwb.supabase.co",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="postgres"
        )

        try:
            # Query products from item_transaksi with usage count
            # Use parameterized query for security
            query = """
                SELECT
                    it.nama_produk as name,
                    it.satuan as unit,
                    MAX(it.harga_satuan) as last_price,
                    COUNT(*) as usage_count
                FROM public.item_transaksi it
                JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                WHERE th.tenant_id = $1
                  AND LOWER(it.nama_produk) LIKE LOWER($2)
                GROUP BY it.nama_produk, it.satuan
                ORDER BY usage_count DESC, it.nama_produk ASC
                LIMIT $3
            """

            search_pattern = f"%{q}%"
            rows = await conn.fetch(query, tenant_id, search_pattern, limit)

            suggestions = [
                ProductSuggestion(
                    name=row['name'],
                    unit=row['unit'] or 'pcs',
                    last_price=int(row['last_price']) if row['last_price'] else None,
                    usage_count=int(row['usage_count'])
                )
                for row in rows
            ]

            logger.info(f"Product search: q='{q}', tenant={tenant_id}, found={len(suggestions)}")

            return ProductSearchResponse(suggestions=suggestions)

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Product search error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "products_router"}
