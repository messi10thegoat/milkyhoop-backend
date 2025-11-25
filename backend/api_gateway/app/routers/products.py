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


class RegisterBarcodeRequest(BaseModel):
    """Request to register barcode to product"""
    barcode: str


class RegisterBarcodeResponse(BaseModel):
    """Response from barcode registration"""
    success: bool
    product: dict
    name_changed: bool = False
    original_name: Optional[str] = None
    message: str


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


@router.patch("/{product_id}/barcode", response_model=RegisterBarcodeResponse)
async def register_barcode(
    request: Request,
    product_id: str,
    body: RegisterBarcodeRequest
):
    """
    Register barcode to existing product.

    Flow:
    1. Validate barcode format (must be exactly 13 digits)
    2. Validate product exists and belongs to tenant
    3. Check if barcode already used by another product
    4. Save barcode to product
    5. Update chat history receipts with new barcode

    Response includes name_changed flag for future central DB lookup feature.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        user_id = request.state.user.get("user_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Validate barcode format - must be exactly 13 digits
        barcode = body.barcode.strip()
        if not barcode:
            raise HTTPException(status_code=400, detail="Barcode cannot be empty")

        if not barcode.isdigit():
            raise HTTPException(status_code=400, detail="Barcode harus berupa angka saja")

        if len(barcode) != 13:
            raise HTTPException(status_code=400, detail=f"Barcode harus 13 digit (EAN-13). Anda memasukkan {len(barcode)} digit.")

        # Connect to database
        conn = await asyncpg.connect(
            host="db.ltrqrejrkbusvmknpnwb.supabase.co",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="postgres"
        )

        try:
            # Step 1: Check if product exists and belongs to tenant
            product_query = """
                SELECT id, nama_produk, satuan, kategori, harga_jual, deskripsi, barcode
                FROM public.products
                WHERE id = $1 AND tenant_id = $2
            """
            product = await conn.fetchrow(product_query, product_id, tenant_id)

            if not product:
                raise HTTPException(
                    status_code=404,
                    detail=f"Product '{product_id}' not found or does not belong to your tenant"
                )

            original_name = product['nama_produk']

            # Step 2: Check if barcode is already used by another product
            duplicate_query = """
                SELECT id, nama_produk
                FROM public.products
                WHERE barcode = $1 AND id != $2
            """
            duplicate = await conn.fetchrow(duplicate_query, barcode, product_id)

            if duplicate:
                raise HTTPException(
                    status_code=409,
                    detail=f"Barcode '{barcode}' already registered to product '{duplicate['nama_produk']}'"
                )

            # Step 3: Update product with barcode
            # Note: For Phase 2, add central DB lookup here to auto-replace name
            update_query = """
                UPDATE public.products
                SET barcode = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3
                RETURNING id, nama_produk, satuan, kategori, harga_jual, deskripsi, barcode
            """
            updated = await conn.fetchrow(update_query, barcode, product_id, tenant_id)

            if not updated:
                raise HTTPException(status_code=500, detail="Failed to update product")

            # Build response
            result_product = {
                "id": str(updated['id']),
                "nama_produk": updated['nama_produk'],
                "satuan": updated['satuan'] or 'pcs',
                "kategori": updated['kategori'],
                "harga_jual": float(updated['harga_jual']) if updated['harga_jual'] else None,
                "deskripsi": updated['deskripsi'],
                "barcode": updated['barcode']
            }

            logger.info(f"Barcode registered: product={product_id}, barcode={barcode}, tenant={tenant_id}")

            # Step 4: Update chat history to replace "Daftarkan Barcode" buttons with registered barcode
            # This ensures persistence across page refreshes
            # Table name is chat_messages (from Prisma @@map)
            update_chat_query = """
                UPDATE public.chat_messages
                SET response = REGEXP_REPLACE(
                    response,
                    '<div data-product-id="' || $1 || '" data-product-name="[^"]*"[^>]*class="barcode-register-btn"[^>]*>❎ Daftarkan Barcode</div>',
                    '<div style="font-size:12px;color:#10b981;margin-top:4px">✅ Barcode: ' || $2 || '</div>',
                    'g'
                )
                WHERE tenant_id = $3
                  AND response LIKE '%data-product-id="' || $1 || '"%'
            """
            try:
                await conn.execute(update_chat_query, product_id, barcode, tenant_id)
                logger.info(f"Chat history updated for barcode registration: product={product_id}")
            except Exception as chat_err:
                # Non-fatal: log but don't fail the request
                logger.warning(f"Failed to update chat history: {chat_err}")

            return RegisterBarcodeResponse(
                success=True,
                product=result_product,
                name_changed=False,  # Will be True in Phase 2 when central lookup is implemented
                original_name=original_name,
                message=f"Barcode '{barcode}' berhasil didaftarkan ke produk '{updated['nama_produk']}'"
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Register barcode error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to register barcode: {str(e)}")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "products_router"}
