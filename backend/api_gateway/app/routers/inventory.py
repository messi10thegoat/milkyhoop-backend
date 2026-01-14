"""
Inventory Router - Product Management & Stock Operations
Source: Products table + Persediaan (stock ledger)
Port: Uses gRPC inventory_service:7040
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncpg
import grpc

# Import gRPC stubs
from backend.api_gateway.libs.milkyhoop_protos import inventory_service_pb2, inventory_service_pb2_grpc

# Import centralized config
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# gRPC channel configuration
INVENTORY_SERVICE_HOST = "inventory_service:7040"


# Database connection helper - uses centralized config
async def get_db_connection():
    """Get database connection using environment variables"""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


def get_inventory_stub():
    """Get gRPC stub for inventory service"""
    channel = grpc.insecure_channel(INVENTORY_SERVICE_HOST)
    return inventory_service_pb2_grpc.InventoryServiceStub(channel)


# ========================================
# Response Models
# ========================================

class ProductListItem(BaseModel):
    id: str
    nama_produk: str
    satuan: str
    kategori: Optional[str] = None
    barcode: Optional[str] = None
    harga_jual: Optional[int] = None
    stok: float
    nilai_per_unit: Optional[float] = None
    total_nilai: Optional[float] = None
    minimum_stock: Optional[float] = None
    is_low_stock: bool
    lokasi_gudang: Optional[str] = None


class ProductStockItem(ProductListItem):
    """Extended product info for stock card with updated_at"""
    updated_at: Optional[str] = None


class ProductListResponse(BaseModel):
    products: List[ProductListItem]
    total: int
    has_more: bool


class ProductDetailResponse(BaseModel):
    product: ProductListItem
    margin: Optional[float] = None
    margin_persen: Optional[float] = None
    deskripsi: Optional[str] = None
    created_at: Optional[str] = None
    last_movement_at: Optional[str] = None


class AddProductRequest(BaseModel):
    nama_produk: str
    satuan: str
    kategori: Optional[str] = None
    barcode: Optional[str] = None
    harga_jual: Optional[int] = None
    stok_awal: Optional[float] = 0
    nilai_per_unit: Optional[float] = None
    deskripsi: Optional[str] = None
    minimum_stock: Optional[float] = None


class AddProductResponse(BaseModel):
    success: bool
    message: str
    product_id: str
    nama_produk: str


class StockAdjustmentRequest(BaseModel):
    new_quantity: float
    reason: str  # opname, rusak, hilang, koreksi, lainnya
    notes: Optional[str] = None


class StockAdjustmentResponse(BaseModel):
    success: bool
    message: str
    stok_sebelum: float
    stok_setelah: float
    adjustment_amount: float


class LowStockAlertItem(BaseModel):
    id: str
    nama_produk: str
    satuan: str
    current_stock: float
    minimum_stock: float
    shortfall: float
    days_since_movement: Optional[int] = None


class LowStockAlertsResponse(BaseModel):
    alerts: List[LowStockAlertItem]
    total_count: int


class CategoryListResponse(BaseModel):
    categories: List[str]


class InventorySummaryResponse(BaseModel):
    """Summary counts for inventory dashboard categories"""
    total_products: int      # All products
    melimpah_count: int      # stok > minimum_stock
    menipis_count: int       # 0 < stok <= minimum_stock
    habis_count: int         # stok <= 0
    aset_count: int          # Fixed assets (placeholder)
    reorder_count: int       # Reorder list (placeholder)


class SupplierItem(BaseModel):
    """Supplier info from purchase transactions"""
    nama_supplier: str
    total_purchases: int


class TransactionHistoryItem(BaseModel):
    """Transaction history for stock card"""
    id: str
    tanggal: str
    jenis_transaksi: str  # pembelian, penjualan
    jumlah: float
    satuan: str  # unit used in this transaction (e.g., Dus, pcs)
    harga_satuan: float
    subtotal: float
    nama_pihak: Optional[str] = None


class StockInsight(BaseModel):
    """Aggregated insights for a product"""
    total_masuk: float
    total_keluar: float
    rata_rata_penjualan: Optional[float] = None
    jumlah_transaksi_penjualan: int


class ProductStockCardResponse(BaseModel):
    """Complete stock card data for a product"""
    product: ProductStockItem
    minimum_stock: Optional[float] = None
    suppliers: List[SupplierItem]
    transaction_history: List[TransactionHistoryItem]
    insight: StockInsight
    # Unit conversion fields (V007)
    base_unit: Optional[str] = None           # e.g., "pcs" (smallest sellable unit)
    wholesale_unit: Optional[str] = None      # e.g., "dus" (bulk purchase unit)
    units_per_wholesale: Optional[int] = None  # e.g., 12 (1 dus = 12 pcs)
    # Legacy aliases for backward compatibility
    units_per_pack: Optional[int] = None      # same as units_per_wholesale
    content_unit: Optional[str] = None        # same as base_unit
    stok_satuan_terkecil: Optional[float] = None  # stock already in base unit after V008


# ========================================
# Endpoints
# ========================================

@router.get("/products", response_model=ProductListResponse)
async def list_products(
    request: Request,
    search: Optional[str] = Query(None, description="Search by name or barcode"),
    kategori: Optional[str] = Query(None, description="Filter by category"),
    low_stock_only: bool = Query(False, description="Only show low stock items"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """
    List all products with stock information.
    Supports search, category filter, and pagination.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()

        try:
            # Build query with optional filters
            # Only show items with track_inventory=true in inventory views
            where_clauses = ["p.tenant_id = $1", "COALESCE(p.track_inventory, true) = true"]
            params = [tenant_id]
            param_idx = 2

            if search:
                where_clauses.append(f"(LOWER(p.nama_produk) LIKE LOWER(${param_idx}) OR p.barcode = ${param_idx + 1})")
                params.append(f"%{search}%")
                params.append(search)
                param_idx += 2

            if kategori:
                where_clauses.append(f"p.kategori = ${param_idx}")
                params.append(kategori)
                param_idx += 1

            if low_stock_only:
                where_clauses.append("s.jumlah < COALESCE(s.minimum_stock, 0)")

            where_sql = " AND ".join(where_clauses)

            # Count total
            count_query = f"""
                SELECT COUNT(DISTINCT p.id)
                FROM public.products p
                LEFT JOIN public.persediaan s ON p.id = s.product_id AND p.tenant_id = s.tenant_id
                WHERE {where_sql}
            """
            total = await conn.fetchval(count_query, *params)

            # Fetch products with stock
            query = f"""
                SELECT
                    p.id,
                    p.nama_produk,
                    p.satuan,
                    p.kategori,
                    p.barcode,
                    p.harga_jual,
                    p.deskripsi,
                    COALESCE(s.jumlah, 0) as stok,
                    s.nilai_per_unit,
                    COALESCE(s.jumlah * s.nilai_per_unit, 0) as total_nilai,
                    s.minimum_stock,
                    CASE WHEN s.jumlah < COALESCE(s.minimum_stock, 0) THEN true ELSE false END as is_low_stock,
                    s.lokasi_gudang
                FROM public.products p
                LEFT JOIN public.persediaan s ON p.id = s.product_id AND p.tenant_id = s.tenant_id
                WHERE {where_sql}
                ORDER BY p.nama_produk ASC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, offset])
            rows = await conn.fetch(query, *params)

            products = [
                ProductListItem(
                    id=str(row['id']),
                    nama_produk=row['nama_produk'],
                    satuan=row['satuan'] or 'pcs',
                    kategori=row['kategori'],
                    barcode=row['barcode'],
                    harga_jual=int(row['harga_jual']) if row['harga_jual'] else None,
                    stok=float(row['stok']),
                    nilai_per_unit=float(row['nilai_per_unit']) if row['nilai_per_unit'] else None,
                    total_nilai=float(row['total_nilai']) if row['total_nilai'] else None,
                    minimum_stock=float(row['minimum_stock']) if row['minimum_stock'] else None,
                    is_low_stock=row['is_low_stock'],
                    lokasi_gudang=row['lokasi_gudang']
                )
                for row in rows
            ]

            logger.info(f"List products: tenant={tenant_id}, search={search}, total={total}, returned={len(products)}")

            return ProductListResponse(
                products=products,
                total=total,
                has_more=(offset + limit) < total
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List products error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.get("/products/{product_id}", response_model=ProductDetailResponse)
async def get_product_detail(
    request: Request,
    product_id: str
):
    """
    Get detailed product information including stock and margin.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()

        try:
            query = """
                SELECT
                    p.id,
                    p.nama_produk,
                    p.satuan,
                    p.kategori,
                    p.barcode,
                    p.harga_jual,
                    p.deskripsi,
                    p.created_at,
                    COALESCE(s.jumlah, 0) as stok,
                    s.nilai_per_unit,
                    COALESCE(s.jumlah * s.nilai_per_unit, 0) as total_nilai,
                    s.minimum_stock,
                    CASE WHEN s.jumlah < COALESCE(s.minimum_stock, 0) THEN true ELSE false END as is_low_stock,
                    s.lokasi_gudang,
                    s.last_movement_at
                FROM public.products p
                LEFT JOIN public.persediaan s ON p.id = s.product_id AND p.tenant_id = s.tenant_id
                WHERE p.id = $1 AND p.tenant_id = $2
                LIMIT 1
            """
            row = await conn.fetchrow(query, product_id, tenant_id)

            if not row:
                raise HTTPException(status_code=404, detail="Product not found")

            # Calculate margin
            margin = None
            margin_persen = None
            if row['harga_jual'] and row['nilai_per_unit'] and row['nilai_per_unit'] > 0:
                margin = float(row['harga_jual']) - float(row['nilai_per_unit'])
                margin_persen = round((margin / float(row['nilai_per_unit'])) * 100, 1)

            product = ProductListItem(
                id=str(row['id']),
                nama_produk=row['nama_produk'],
                satuan=row['satuan'] or 'pcs',
                kategori=row['kategori'],
                barcode=row['barcode'],
                harga_jual=int(row['harga_jual']) if row['harga_jual'] else None,
                stok=float(row['stok']),
                nilai_per_unit=float(row['nilai_per_unit']) if row['nilai_per_unit'] else None,
                total_nilai=float(row['total_nilai']) if row['total_nilai'] else None,
                minimum_stock=float(row['minimum_stock']) if row['minimum_stock'] else None,
                is_low_stock=row['is_low_stock'],
                lokasi_gudang=row['lokasi_gudang']
            )

            return ProductDetailResponse(
                product=product,
                margin=margin,
                margin_persen=margin_persen,
                deskripsi=row['deskripsi'],
                created_at=row['created_at'].isoformat() if row['created_at'] else None,
                last_movement_at=row['last_movement_at'].isoformat() if row['last_movement_at'] else None
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get product detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch product")


@router.post("/products", response_model=AddProductResponse)
async def add_product(
    request: Request,
    body: AddProductRequest
):
    """
    Add a new product to inventory.
    Creates entry in Products table and optionally initializes stock in Persediaan.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        user_id = request.state.user.get("user_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Validate required fields
        if not body.nama_produk or not body.nama_produk.strip():
            raise HTTPException(status_code=400, detail="Nama produk wajib diisi")

        if not body.satuan or not body.satuan.strip():
            raise HTTPException(status_code=400, detail="Satuan wajib diisi")

        conn = await get_db_connection()

        try:
            # Check for duplicate product name
            duplicate_query = """
                SELECT id FROM public.products
                WHERE tenant_id = $1 AND LOWER(nama_produk) = LOWER($2)
            """
            duplicate = await conn.fetchrow(duplicate_query, tenant_id, body.nama_produk.strip())
            if duplicate:
                raise HTTPException(
                    status_code=409,
                    detail=f"Produk '{body.nama_produk}' sudah ada"
                )

            # Check for duplicate barcode if provided
            if body.barcode:
                barcode_query = """
                    SELECT id, nama_produk FROM public.products
                    WHERE tenant_id = $1 AND barcode = $2
                """
                barcode_dup = await conn.fetchrow(barcode_query, tenant_id, body.barcode)
                if barcode_dup:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Barcode '{body.barcode}' sudah terdaftar untuk produk '{barcode_dup['nama_produk']}'"
                    )

            # Insert product
            insert_query = """
                INSERT INTO public.products (
                    tenant_id, nama_produk, satuan, kategori, barcode, harga_jual, deskripsi
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id, nama_produk
            """
            row = await conn.fetchrow(
                insert_query,
                tenant_id,
                body.nama_produk.strip(),
                body.satuan.strip(),
                body.kategori,
                body.barcode,
                body.harga_jual,
                body.deskripsi
            )

            product_id = str(row['id'])

            # If initial stock provided, create Persediaan entry
            if body.stok_awal and body.stok_awal > 0:
                persediaan_query = """
                    INSERT INTO public.persediaan (
                        tenant_id, product_id, lokasi_gudang, jumlah, nilai_per_unit, minimum_stock, last_movement_at
                    ) VALUES ($1, $2, 'utama', $3, $4, $5, NOW())
                """
                await conn.execute(
                    persediaan_query,
                    tenant_id,
                    product_id,
                    body.stok_awal,
                    body.nilai_per_unit or 0,
                    body.minimum_stock or 0
                )

            logger.info(f"Product created: id={product_id}, name={body.nama_produk}, tenant={tenant_id}")

            return AddProductResponse(
                success=True,
                message=f"Produk '{body.nama_produk}' berhasil ditambahkan",
                product_id=product_id,
                nama_produk=body.nama_produk
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add product error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to add product: {str(e)}")


@router.post("/products/{product_id}/adjust", response_model=StockAdjustmentResponse)
async def adjust_stock(
    request: Request,
    product_id: str,
    body: StockAdjustmentRequest
):
    """
    Adjust stock level for a product.
    Uses gRPC inventory_service.AdjustStock for proper audit trail.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        user_id = request.state.user.get("user_id", "unknown")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Validate reason
        valid_reasons = ['opname', 'rusak', 'hilang', 'koreksi', 'lainnya']
        if body.reason not in valid_reasons:
            raise HTTPException(
                status_code=400,
                detail=f"Alasan tidak valid. Pilih salah satu: {', '.join(valid_reasons)}"
            )

        # Get product name for gRPC call (inventory_service uses produk_id as name)
        conn = await get_db_connection()
        try:
            product_query = """
                SELECT nama_produk FROM public.products
                WHERE id = $1 AND tenant_id = $2
            """
            product = await conn.fetchrow(product_query, product_id, tenant_id)
            if not product:
                raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

            product_name = product['nama_produk']
        finally:
            await conn.close()

        # Call gRPC inventory_service
        stub = get_inventory_stub()
        grpc_request = inventory_service_pb2.AdjustStockRequest(
            tenant_id=tenant_id,
            produk_id=product_name,  # inventory_service uses product name
            lokasi_gudang="utama",
            new_quantity=body.new_quantity,
            reason=body.reason,
            keterangan=body.notes or "",
            created_by=user_id
        )

        try:
            response = stub.AdjustStock(grpc_request, timeout=10)

            if not response.success:
                raise HTTPException(status_code=400, detail=response.message)

            logger.info(f"Stock adjusted: product={product_id}, reason={body.reason}, before={response.stok_sebelum}, after={response.stok_setelah}")

            return StockAdjustmentResponse(
                success=True,
                message=response.message,
                stok_sebelum=response.stok_sebelum,
                stok_setelah=response.stok_setelah,
                adjustment_amount=response.adjustment_amount
            )

        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()}: {e.details()}")
            raise HTTPException(status_code=500, detail=f"Inventory service error: {e.details()}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Adjust stock error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to adjust stock")


@router.get("/low-stock", response_model=LowStockAlertsResponse)
async def get_low_stock_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200)
):
    """
    Get all products with low stock (below minimum threshold).
    Uses gRPC inventory_service.GetLowStockAlerts.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        stub = get_inventory_stub()
        grpc_request = inventory_service_pb2.GetLowStockAlertsRequest(
            tenant_id=tenant_id,
            limit=limit
        )

        try:
            response = stub.GetLowStockAlerts(grpc_request, timeout=10)

            # Map gRPC response to REST response with product IDs
            # Need to lookup product IDs by name
            conn = await get_db_connection()
            try:
                alerts = []
                for alert in response.alerts:
                    # Lookup product ID
                    product_query = """
                        SELECT id FROM public.products
                        WHERE tenant_id = $1 AND nama_produk = $2
                        LIMIT 1
                    """
                    product = await conn.fetchrow(product_query, tenant_id, alert.produk_id)
                    product_id = str(product['id']) if product else alert.produk_id

                    alerts.append(LowStockAlertItem(
                        id=product_id,
                        nama_produk=alert.produk_id,
                        satuan=alert.satuan,
                        current_stock=alert.current_stock,
                        minimum_stock=alert.minimum_stock,
                        shortfall=alert.shortfall,
                        days_since_movement=alert.days_since_movement if alert.days_since_movement > 0 else None
                    ))
            finally:
                await conn.close()

            return LowStockAlertsResponse(
                alerts=alerts,
                total_count=response.total_count
            )

        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()}: {e.details()}")
            raise HTTPException(status_code=500, detail=f"Inventory service error: {e.details()}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get low stock alerts error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch low stock alerts")


@router.get("/categories", response_model=CategoryListResponse)
async def get_categories(
    request: Request
):
    """
    Get list of categories used by this tenant.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()

        try:
            query = """
                SELECT DISTINCT kategori
                FROM public.products
                WHERE tenant_id = $1 AND kategori IS NOT NULL AND kategori != ''
                ORDER BY kategori ASC
            """
            rows = await conn.fetch(query, tenant_id)
            categories = [row['kategori'] for row in rows]

            return CategoryListResponse(categories=categories)

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get categories error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch categories")


@router.get("/summary", response_model=InventorySummaryResponse)
async def get_inventory_summary(request: Request):
    """
    Get summary counts for inventory dashboard categories.
    Returns counts for: total, melimpah (>=24), menipis (0<stok<12), habis (<=0)
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()

        try:
            # Single query with CASE WHEN to count all categories
            # Uses minimum_stock for dynamic thresholds instead of hardcoded values
            # Only count items with track_inventory=true
            query = """
                SELECT
                    COUNT(*) as total_products,
                    COUNT(CASE WHEN COALESCE(s.jumlah, 0) > COALESCE(s.minimum_stock, 0) THEN 1 END) as melimpah_count,
                    COUNT(CASE WHEN COALESCE(s.jumlah, 0) > 0 AND COALESCE(s.jumlah, 0) <= COALESCE(s.minimum_stock, 0) THEN 1 END) as menipis_count,
                    COUNT(CASE WHEN COALESCE(s.jumlah, 0) <= 0 THEN 1 END) as habis_count
                FROM public.products p
                LEFT JOIN public.persediaan s ON p.id = s.product_id AND p.tenant_id = s.tenant_id
                WHERE p.tenant_id = $1 AND COALESCE(p.track_inventory, true) = true
            """
            row = await conn.fetchrow(query, tenant_id)

            logger.info(f"Inventory summary: tenant={tenant_id}, total={row['total_products']}")

            return InventorySummaryResponse(
                total_products=row['total_products'] or 0,
                melimpah_count=row['melimpah_count'] or 0,
                menipis_count=row['menipis_count'] or 0,
                habis_count=row['habis_count'] or 0,
                aset_count=0,      # Placeholder - different table
                reorder_count=0    # Placeholder - not implemented yet
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get inventory summary error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch inventory summary")


@router.get("/products/{product_id}/stock-card", response_model=ProductStockCardResponse)
async def get_product_stock_card(request: Request, product_id: str):
    """
    Get comprehensive stock card data for a product.
    Includes: product details, suppliers, transaction history, and insights.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()

        try:
            # 1. Get product details with stock info + unit conversion (V007)
            product_query = """
                SELECT
                    p.id, p.nama_produk, p.satuan, p.kategori, p.barcode,
                    p.harga_jual, s.nilai_per_unit, p.deskripsi,
                    COALESCE(s.jumlah, 0) as stok,
                    s.minimum_stock,
                    (COALESCE(s.jumlah, 0) * COALESCE(s.nilai_per_unit, 0)) as total_nilai,
                    COALESCE(s.jumlah, 0) <= COALESCE(s.minimum_stock, 0) AND COALESCE(s.jumlah, 0) > 0 as is_low_stock,
                    s.lokasi_gudang,
                    p.updated_at,
                    -- V007 unit conversion fields
                    p.base_unit,
                    p.wholesale_unit,
                    p.units_per_wholesale
                FROM public.products p
                LEFT JOIN public.persediaan s ON p.id = s.product_id AND p.tenant_id = s.tenant_id
                WHERE p.id = $1 AND p.tenant_id = $2
            """
            product_row = await conn.fetchrow(product_query, product_id, tenant_id)

            if not product_row:
                raise HTTPException(status_code=404, detail="Product not found")

            product = ProductStockItem(
                id=product_row['id'],
                nama_produk=product_row['nama_produk'],
                satuan=product_row['satuan'],
                kategori=product_row['kategori'],
                barcode=product_row['barcode'],
                harga_jual=product_row['harga_jual'],
                stok=float(product_row['stok']),
                nilai_per_unit=float(product_row['nilai_per_unit']) if product_row['nilai_per_unit'] else None,
                total_nilai=float(product_row['total_nilai']) if product_row['total_nilai'] else None,
                minimum_stock=float(product_row['minimum_stock']) if product_row['minimum_stock'] else None,
                is_low_stock=product_row['is_low_stock'] or False,
                lokasi_gudang=product_row['lokasi_gudang'],
                updated_at=product_row['updated_at'].isoformat() if product_row['updated_at'] else None
            )
            nama_produk = product_row['nama_produk']

            # 2. Get suppliers from purchase transactions
            suppliers_query = """
                SELECT
                    th.nama_pihak as nama_supplier,
                    COUNT(*) as total_purchases
                FROM public.transaksi_harian th
                JOIN public.item_transaksi it ON th.id = it.transaksi_id
                WHERE th.tenant_id = $1
                    AND LOWER(it.nama_produk) = LOWER($2)
                    AND th.jenis_transaksi = 'pembelian'
                    AND th.nama_pihak IS NOT NULL
                    AND th.nama_pihak != ''
                GROUP BY th.nama_pihak
                ORDER BY total_purchases DESC
            """
            supplier_rows = await conn.fetch(suppliers_query, tenant_id, nama_produk)
            suppliers = [
                SupplierItem(
                    nama_supplier=row['nama_supplier'],
                    total_purchases=row['total_purchases']
                )
                for row in supplier_rows
            ]

            # 3. Get transaction history (last 10)
            history_query = """
                SELECT
                    th.id,
                    TO_CHAR(th.created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD') as tanggal,
                    th.jenis_transaksi,
                    it.jumlah,
                    it.satuan,
                    it.harga_satuan,
                    it.subtotal,
                    th.nama_pihak
                FROM public.transaksi_harian th
                JOIN public.item_transaksi it ON th.id = it.transaksi_id
                WHERE th.tenant_id = $1
                    AND LOWER(it.nama_produk) = LOWER($2)
                ORDER BY th.created_at DESC
                LIMIT 10
            """
            history_rows = await conn.fetch(history_query, tenant_id, nama_produk)
            transaction_history = [
                TransactionHistoryItem(
                    id=row['id'],
                    tanggal=row['tanggal'],
                    jenis_transaksi=row['jenis_transaksi'],
                    jumlah=float(row['jumlah']),
                    satuan=row['satuan'] or product_row['satuan'],
                    harga_satuan=float(row['harga_satuan']),
                    subtotal=float(row['subtotal']),
                    nama_pihak=row['nama_pihak']
                )
                for row in history_rows
            ]

            # 4. Get insight aggregates
            insight_query = """
                SELECT
                    COALESCE(SUM(CASE WHEN th.jenis_transaksi = 'pembelian' THEN it.jumlah ELSE 0 END), 0) as total_masuk,
                    COALESCE(SUM(CASE WHEN th.jenis_transaksi = 'penjualan' THEN it.jumlah ELSE 0 END), 0) as total_keluar,
                    AVG(CASE WHEN th.jenis_transaksi = 'penjualan' THEN it.jumlah END) as rata_rata_penjualan,
                    COUNT(CASE WHEN th.jenis_transaksi = 'penjualan' THEN 1 END) as jumlah_transaksi_penjualan
                FROM public.transaksi_harian th
                JOIN public.item_transaksi it ON th.id = it.transaksi_id
                WHERE th.tenant_id = $1
                    AND LOWER(it.nama_produk) = LOWER($2)
            """
            insight_row = await conn.fetchrow(insight_query, tenant_id, nama_produk)
            insight = StockInsight(
                total_masuk=float(insight_row['total_masuk']),
                total_keluar=float(insight_row['total_keluar']),
                rata_rata_penjualan=float(insight_row['rata_rata_penjualan']) if insight_row['rata_rata_penjualan'] else None,
                jumlah_transaksi_penjualan=insight_row['jumlah_transaksi_penjualan']
            )

            # 5. Unit conversion - now from V007 fields in products table
            # No need to calculate from transactions anymore
            base_unit = product_row['base_unit'] or 'pcs'
            wholesale_unit = product_row['wholesale_unit']
            units_per_wholesale = product_row['units_per_wholesale']

            # Stock is already in base unit after V008 migration
            # So stok_satuan_terkecil = stok (no multiplication needed)
            stok_satuan_terkecil = float(product_row['stok'])

            logger.info(f"Stock card retrieved: product={nama_produk}, tenant={tenant_id}, units_per_wholesale={units_per_wholesale}, stok={stok_satuan_terkecil} {base_unit}")

            return ProductStockCardResponse(
                product=product,
                minimum_stock=float(product_row['minimum_stock']) if product_row['minimum_stock'] else None,
                suppliers=suppliers,
                transaction_history=transaction_history,
                insight=insight,
                # V007 fields
                base_unit=base_unit,
                wholesale_unit=wholesale_unit,
                units_per_wholesale=units_per_wholesale,
                # Legacy aliases for backward compatibility
                units_per_pack=units_per_wholesale,
                content_unit=base_unit,
                stok_satuan_terkecil=stok_satuan_terkecil
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get product stock card error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch product stock card")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "inventory_router"}
