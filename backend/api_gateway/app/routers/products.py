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


# POS Product Search Response (for selling - needs id, barcode, harga_jual)
class POSProductItem(BaseModel):
    id: str
    name: str
    barcode: Optional[str] = None
    harga_jual: int  # Selling price
    stok: Optional[int] = None


class POSProductSearchResponse(BaseModel):
    products: List[POSProductItem]


class ProductSuggestion(BaseModel):
    name: str
    unit: str
    last_price: Optional[int] = None
    usage_count: int
    # Auto-fill fields from last transaction
    harga_jual: Optional[int] = None  # Selling price
    units_per_pack: Optional[int] = None  # Units per pack (derived from hpp)


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
    # Auto-fill fields for Kulakan form (from last transaction)
    units_per_pack: Optional[int] = None
    harga_jual: Optional[int] = None
    last_price: Optional[int] = None


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

            product_name = row['name']

            # Also fetch last transaction data for auto-fill (Bug #5: include satuan from history)
            last_tx_query = """
                SELECT
                    it.satuan as last_unit,
                    it.harga_satuan as last_price,
                    it.harga_jual,
                    it.hpp_per_unit
                FROM public.item_transaksi it
                JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                WHERE th.tenant_id = $1
                  AND it.nama_produk = $2
                  AND th.jenis_transaksi = 'pembelian'
                ORDER BY th.created_at DESC
                LIMIT 1
            """
            last_tx = await conn.fetchrow(last_tx_query, tenant_id, product_name)

            # Compute units_per_pack from last_price / hpp_per_unit
            units_per_pack = None
            harga_jual = None
            last_price = None
            last_unit = None  # Bug #5: Get unit from last purchase transaction

            if last_tx:
                last_price = int(last_tx['last_price']) if last_tx['last_price'] else None
                harga_jual = int(last_tx['harga_jual']) if last_tx['harga_jual'] else None
                last_unit = last_tx['last_unit']  # Bug #5: Satuan from history
                if last_tx['hpp_per_unit'] and last_tx['hpp_per_unit'] > 0 and last_tx['last_price']:
                    computed = last_tx['last_price'] / last_tx['hpp_per_unit']
                    if computed >= 1:
                        units_per_pack = int(round(computed))

            # Bug #5: Use last_unit from history if available (for Kulakan), fallback to product unit
            effective_unit = last_unit if last_unit else (row['unit'] or 'pcs')

            result = BarcodeProduct(
                id=str(row['id']),
                name=row['name'],
                unit=effective_unit,
                category=row['category'],
                price=float(row['price']) if row['price'] else None,
                description=row['description'],
                barcode=row['barcode'],
                units_per_pack=units_per_pack,
                harga_jual=harga_jual,
                last_price=last_price
            )

            logger.info(f"Barcode lookup: barcode={barcode}, tenant={tenant_id}, found={result.name}, units_per_pack={units_per_pack}, harga_jual={harga_jual}")

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
            # Include harga_jual and computed units_per_pack for auto-fill
            # Uses window function to get the latest transaction's data
            query = """
                WITH ranked AS (
                    SELECT
                        it.nama_produk as name,
                        it.satuan as unit,
                        it.harga_satuan as price,
                        it.harga_jual,
                        it.hpp_per_unit,
                        th.created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY it.nama_produk, it.satuan
                            ORDER BY th.created_at DESC
                        ) as rn
                    FROM public.item_transaksi it
                    JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                    WHERE th.tenant_id = $1
                      AND LOWER(it.nama_produk) LIKE LOWER($2)
                )
                SELECT
                    r.name,
                    r.unit,
                    r.price as last_price,
                    r.harga_jual,
                    r.hpp_per_unit,
                    (SELECT COUNT(*) FROM public.item_transaksi it2
                     JOIN public.transaksi_harian th2 ON it2.transaksi_id = th2.id
                     WHERE th2.tenant_id = $1
                       AND it2.nama_produk = r.name
                       AND it2.satuan = r.unit) as usage_count
                FROM ranked r
                WHERE r.rn = 1
                ORDER BY usage_count DESC, r.name ASC
                LIMIT $3
            """

            search_pattern = f"%{q}%"
            rows = await conn.fetch(query, tenant_id, search_pattern, limit)

            suggestions = []
            for row in rows:
                # Compute units_per_pack from price / hpp_per_unit
                units_per_pack = None
                if row['hpp_per_unit'] and row['hpp_per_unit'] > 0 and row['last_price']:
                    computed = row['last_price'] / row['hpp_per_unit']
                    if computed >= 1:
                        units_per_pack = int(round(computed))

                suggestions.append(ProductSuggestion(
                    name=row['name'],
                    unit=row['unit'] or 'pcs',
                    last_price=int(row['last_price']) if row['last_price'] else None,
                    usage_count=int(row['usage_count']),
                    harga_jual=int(row['harga_jual']) if row['harga_jual'] else None,
                    units_per_pack=units_per_pack
                ))

            logger.info(f"Product search: q='{q}', tenant={tenant_id}, found={len(suggestions)}")

            return ProductSearchResponse(suggestions=suggestions)

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Product search error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/search/pos", response_model=POSProductSearchResponse)
async def search_products_for_pos(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Search products for POS (selling)

    Source: Products table (actual inventory)
    Returns: id, name, barcode, harga_jual (selling price), stok

    Only products with harga_jual > 0 can be sold
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
            # Query products from products table (actual inventory)
            # Only include products with harga_jual set
            # Note: stok is in persediaan table, not needed for search
            query = """
                SELECT
                    id,
                    nama_produk as name,
                    barcode,
                    COALESCE(harga_jual, 0) as harga_jual
                FROM public.products
                WHERE tenant_id = $1
                  AND LOWER(nama_produk) LIKE LOWER($2)
                  AND harga_jual IS NOT NULL
                  AND harga_jual > 0
                ORDER BY nama_produk ASC
                LIMIT $3
            """

            search_pattern = f"%{q}%"
            rows = await conn.fetch(query, tenant_id, search_pattern, limit)

            products = [
                POSProductItem(
                    id=str(row['id']),
                    name=row['name'],
                    barcode=row['barcode'],
                    harga_jual=int(row['harga_jual']),
                    stok=None  # Stock lookup not needed for search
                )
                for row in rows
            ]

            logger.info(f"POS product search: q='{q}', tenant={tenant_id}, found={len(products)}")

            return POSProductSearchResponse(products=products)

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"POS product search error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


class RecentSalesProduct(BaseModel):
    id: str
    name: str
    barcode: Optional[str] = None
    price: int


class RecentSalesResponse(BaseModel):
    products: List[RecentSalesProduct]


@router.get("/recent-sales", response_model=RecentSalesResponse)
async def get_recent_sales_products(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
    tenant_id: str = Query(None, description="Optional tenant_id override")
):
    """
    Get recently sold products for quick-add in POS.
    Returns products sorted by most recent sale.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        effective_tenant = tenant_id or request.state.user.get("tenant_id")
        if not effective_tenant:
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
            # Query recent sales from item_transaksi
            query = """
                SELECT DISTINCT ON (p.id)
                    p.id,
                    p.nama_produk as name,
                    p.barcode,
                    COALESCE(p.harga_jual, it.harga_satuan, 0) as price
                FROM public.item_transaksi it
                JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                JOIN public.products p ON it.product_id = p.id
                WHERE th.tenant_id = $1
                  AND th.jenis_transaksi = 'penjualan'
                  AND p.harga_jual IS NOT NULL
                  AND p.harga_jual > 0
                ORDER BY p.id, th.created_at DESC
                LIMIT $2
            """

            rows = await conn.fetch(query, effective_tenant, limit)

            products = [
                RecentSalesProduct(
                    id=str(row['id']),
                    name=row['name'],
                    barcode=row['barcode'],
                    price=int(row['price'])
                )
                for row in rows
            ]

            logger.info(f"Recent sales: tenant={effective_tenant}, found={len(products)}")

            return RecentSalesResponse(products=products)

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Recent sales error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch recent sales")


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
