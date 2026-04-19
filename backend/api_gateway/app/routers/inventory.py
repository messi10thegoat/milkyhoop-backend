"""
Inventory Router - Product Management & Stock Operations
Source: Products table + inventory_ledger (stock movements)
All operations use direct DB queries to inventory_ledger (Pure Ledger).
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncpg

# Import centralized config
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# Database connection helper - uses centralized config
async def get_db_connection():
    """Get database connection using environment variables"""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


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

    total_products: int  # All products
    melimpah_count: int  # stok > minimum_stock
    menipis_count: int  # 0 < stok <= minimum_stock
    habis_count: int  # stok <= 0
    aset_count: int  # Fixed assets (placeholder)
    reorder_count: int  # Reorder list (placeholder)


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
    base_unit: Optional[str] = None  # e.g., "pcs" (smallest sellable unit)
    wholesale_unit: Optional[str] = None  # e.g., "dus" (bulk purchase unit)
    units_per_wholesale: Optional[int] = None  # e.g., 12 (1 dus = 12 pcs)
    # Legacy aliases for backward compatibility
    units_per_pack: Optional[int] = None  # same as units_per_wholesale
    content_unit: Optional[str] = None  # same as base_unit
    stok_satuan_terkecil: Optional[
        float
    ] = None  # stock already in base unit after V008


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
    offset: int = Query(0, ge=0),
):
    """
    List all products with stock information.
    Supports search, category filter, and pagination.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()

        try:
            # Build query with optional filters
            # Only show items with track_inventory=true in inventory views
            where_clauses = [
                "p.tenant_id = $1",
                "COALESCE(p.track_inventory, true) = true",
            ]
            params = [tenant_id]
            param_idx = 2

            if search:
                where_clauses.append(
                    f"(LOWER(p.nama_produk) LIKE LOWER(${param_idx}) OR p.barcode = ${param_idx + 1})"
                )
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
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                        COALESCE((SELECT il2.average_cost FROM inventory_ledger il2
                            WHERE il2.product_id = p.id AND il2.tenant_id = p.tenant_id
                              AND il2.average_cost IS NOT NULL
                            ORDER BY il2.movement_date DESC, il2.created_at DESC LIMIT 1), 0) as nilai_per_unit,
                        0::double precision as minimum_stock
                    FROM inventory_ledger il
                    WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id
                ) s ON true
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
                    CASE WHEN COALESCE(s.jumlah, 0) < COALESCE(s.minimum_stock, 0) THEN true ELSE false END as is_low_stock,
                    NULL::text as lokasi_gudang
                FROM public.products p
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                        COALESCE((SELECT il2.average_cost FROM inventory_ledger il2
                            WHERE il2.product_id = p.id AND il2.tenant_id = p.tenant_id
                              AND il2.average_cost IS NOT NULL
                            ORDER BY il2.movement_date DESC, il2.created_at DESC LIMIT 1), 0) as nilai_per_unit,
                        0::double precision as minimum_stock
                    FROM inventory_ledger il
                    WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id
                ) s ON true
                WHERE {where_sql}
                ORDER BY p.nama_produk ASC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, offset])
            rows = await conn.fetch(query, *params)

            products = [
                ProductListItem(
                    id=str(row["id"]),
                    nama_produk=row["nama_produk"],
                    satuan=row["satuan"] or "pcs",
                    kategori=row["kategori"],
                    barcode=row["barcode"],
                    harga_jual=int(row["harga_jual"]) if row["harga_jual"] else None,
                    stok=float(row["stok"]),
                    nilai_per_unit=float(row["nilai_per_unit"])
                    if row["nilai_per_unit"]
                    else None,
                    total_nilai=float(row["total_nilai"])
                    if row["total_nilai"]
                    else None,
                    minimum_stock=float(row["minimum_stock"])
                    if row["minimum_stock"]
                    else None,
                    is_low_stock=row["is_low_stock"],
                    lokasi_gudang=row["lokasi_gudang"],
                )
                for row in rows
            ]

            logger.info(
                f"List products: tenant={tenant_id}, search={search}, total={total}, returned={len(products)}"
            )

            return ProductListResponse(
                products=products, total=total, has_more=(offset + limit) < total
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List products error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.get("/products/{product_id}", response_model=ProductDetailResponse)
async def get_product_detail(request: Request, product_id: str):
    """
    Get detailed product information including stock and margin.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
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
                    CASE WHEN COALESCE(s.jumlah, 0) < COALESCE(s.minimum_stock, 0) THEN true ELSE false END as is_low_stock,
                    NULL::text as lokasi_gudang,
                    s.last_movement_at
                FROM public.products p
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                        COALESCE((SELECT il2.average_cost FROM inventory_ledger il2
                            WHERE il2.product_id = p.id AND il2.tenant_id = p.tenant_id
                              AND il2.average_cost IS NOT NULL
                            ORDER BY il2.movement_date DESC, il2.created_at DESC LIMIT 1), 0) as nilai_per_unit,
                        0::double precision as minimum_stock,
                        (SELECT MAX(il3.movement_date)::timestamp FROM inventory_ledger il3
                            WHERE il3.product_id = p.id AND il3.tenant_id = p.tenant_id) as last_movement_at
                    FROM inventory_ledger il
                    WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id
                ) s ON true
                WHERE p.id = $1 AND p.tenant_id = $2
                LIMIT 1
            """
            row = await conn.fetchrow(query, product_id, tenant_id)

            if not row:
                raise HTTPException(status_code=404, detail="Product not found")

            # Calculate margin
            margin = None
            margin_persen = None
            if (
                row["harga_jual"]
                and row["nilai_per_unit"]
                and row["nilai_per_unit"] > 0
            ):
                margin = float(row["harga_jual"]) - float(row["nilai_per_unit"])
                margin_persen = round((margin / float(row["nilai_per_unit"])) * 100, 1)

            product = ProductListItem(
                id=str(row["id"]),
                nama_produk=row["nama_produk"],
                satuan=row["satuan"] or "pcs",
                kategori=row["kategori"],
                barcode=row["barcode"],
                harga_jual=int(row["harga_jual"]) if row["harga_jual"] else None,
                stok=float(row["stok"]),
                nilai_per_unit=float(row["nilai_per_unit"])
                if row["nilai_per_unit"]
                else None,
                total_nilai=float(row["total_nilai"]) if row["total_nilai"] else None,
                minimum_stock=float(row["minimum_stock"])
                if row["minimum_stock"]
                else None,
                is_low_stock=row["is_low_stock"],
                lokasi_gudang=row["lokasi_gudang"],
            )

            return ProductDetailResponse(
                product=product,
                margin=margin,
                margin_persen=margin_persen,
                deskripsi=row["deskripsi"],
                created_at=row["created_at"].isoformat() if row["created_at"] else None,
                last_movement_at=row["last_movement_at"].isoformat()
                if row["last_movement_at"]
                else None,
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get product detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch product")


@router.post("/products", response_model=AddProductResponse)
async def add_product(request: Request, body: AddProductRequest):
    """
    Add a new product to inventory.
    Creates entry in Products table and optionally initializes stock via inventory_ledger.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
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
            duplicate = await conn.fetchrow(
                duplicate_query, tenant_id, body.nama_produk.strip()
            )
            if duplicate:
                raise HTTPException(
                    status_code=409, detail=f"Produk '{body.nama_produk}' sudah ada"
                )

            # Check for duplicate barcode if provided
            if body.barcode:
                barcode_query = """
                    SELECT id, nama_produk FROM public.products
                    WHERE tenant_id = $1 AND barcode = $2
                """
                barcode_dup = await conn.fetchrow(
                    barcode_query, tenant_id, body.barcode
                )
                if barcode_dup:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Barcode '{body.barcode}' sudah terdaftar untuk produk '{barcode_dup['nama_produk']}'",
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
                body.deskripsi,
            )

            product_id = str(row["id"])

            # If initial stock provided, create inventory_ledger opening balance entry
            if body.stok_awal and body.stok_awal > 0:
                opening_balance_query = """
                    INSERT INTO inventory_ledger (
                        tenant_id, product_id, product_name, movement_type, movement_date,
                        source_type, quantity_in, quantity_out, quantity_balance,
                        unit_cost, total_cost, average_cost, notes
                    ) VALUES (
                        $1, $2::uuid, $3, 'IN', CURRENT_DATE,
                        'OPENING_BALANCE', $4, 0, $4,
                        $5, $4 * $5, $5, 'Initial stock from product creation'
                    )
                """
                await conn.execute(
                    opening_balance_query,
                    tenant_id,
                    product_id,
                    body.nama_produk.strip(),
                    body.stok_awal,
                    body.nilai_per_unit or 0,
                )

            logger.info(
                f"Product created: id={product_id}, name={body.nama_produk}, tenant={tenant_id}"
            )

            return AddProductResponse(
                success=True,
                message=f"Produk '{body.nama_produk}' berhasil ditambahkan",
                product_id=product_id,
                nama_produk=body.nama_produk,
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add product error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to add product: {str(e)}")


@router.post("/products/{product_id}/adjust")
async def adjust_stock(
    request: Request,
    product_id: str,
):
    """
    DEPRECATED — HTTP 410 Gone.
    This endpoint had NO journal creation (violated Iron Law 1, 3, 8, Inventory Rule 1).
    Use POST /api/items/{product_id}/stock-adjustment instead.
    """
    raise HTTPException(
        status_code=410,
        detail="This endpoint has been removed. Use POST /api/items/{product_id}/stock-adjustment instead.",
    )


@router.get("/low-stock", response_model=LowStockAlertsResponse)
async def get_low_stock_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    include_zero_stock: bool = Query(
        False, description="Include items with zero stock even if no reorder level set"
    ),
):
    """
    Get all products with low stock (below minimum threshold).
    Queries inventory_ledger directly for current stock.
    Uses persediaan.minimum_stock as primary threshold, falls back to
    products.reorder_level if persediaan row doesn't exist.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            query = """
                SELECT
                    p.id::text,
                    p.nama_produk,
                    p.satuan,
                    COALESCE(stock.current_stock, 0) as current_stock,
                    COALESCE(NULLIF(p.reorder_level, 0)::double precision, 0) as minimum_stock,
                    COALESCE(NULLIF(p.reorder_level, 0)::double precision, 0) - COALESCE(stock.current_stock, 0) as shortfall,
                    (CURRENT_DATE - stock.last_movement::date)::int as days_since_movement
                FROM public.products p
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as current_stock,
                        MAX(il.movement_date) as last_movement
                    FROM inventory_ledger il
                    WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id
                ) stock ON true
                WHERE p.tenant_id = $1
                    AND COALESCE(p.track_inventory, true) = true
                    AND (
                        (COALESCE(NULLIF(p.reorder_level, 0)::double precision, 0) > 0
                         AND COALESCE(stock.current_stock, 0) < COALESCE(NULLIF(p.reorder_level, 0)::double precision, 0))
                        OR
                        ($3 = true AND COALESCE(stock.current_stock, 0) <= 0)
                    )
                ORDER BY shortfall DESC
                LIMIT $2
            """
            rows = await conn.fetch(query, tenant_id, limit, include_zero_stock)

            alerts = [
                LowStockAlertItem(
                    id=row["id"],
                    nama_produk=row["nama_produk"],
                    satuan=row["satuan"] or "pcs",
                    current_stock=float(row["current_stock"]),
                    minimum_stock=float(row["minimum_stock"]),
                    shortfall=float(row["shortfall"]),
                    days_since_movement=row["days_since_movement"]
                    if row["days_since_movement"] and row["days_since_movement"] > 0
                    else None,
                )
                for row in rows
            ]

            logger.info(f"Low stock alerts: tenant={tenant_id}, count={len(alerts)}")

            return LowStockAlertsResponse(alerts=alerts, total_count=len(alerts))

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get low stock alerts error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch low stock alerts")


@router.get("/categories", response_model=CategoryListResponse)
async def get_categories(request: Request):
    """
    Get list of categories used by this tenant.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
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
            categories = [row["kategori"] for row in rows]

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
        if not hasattr(request.state, "user") or not request.state.user:
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
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                        0::double precision as minimum_stock
                    FROM inventory_ledger il
                    WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id
                ) s ON true
                WHERE p.tenant_id = $1 AND COALESCE(p.track_inventory, true) = true
            """
            row = await conn.fetchrow(query, tenant_id)

            logger.info(
                f"Inventory summary: tenant={tenant_id}, total={row['total_products']}"
            )

            return InventorySummaryResponse(
                total_products=row["total_products"] or 0,
                melimpah_count=row["melimpah_count"] or 0,
                menipis_count=row["menipis_count"] or 0,
                habis_count=row["habis_count"] or 0,
                aset_count=0,  # Placeholder - different table
                reorder_count=0,  # Placeholder - not implemented yet
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get inventory summary error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch inventory summary")


@router.get(
    "/products/{product_id}/stock-card", response_model=ProductStockCardResponse
)
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
                    NULL::text as lokasi_gudang,
                    p.updated_at,
                    -- V007 unit conversion fields
                    p.base_unit,
                    p.wholesale_unit,
                    p.units_per_wholesale
                FROM public.products p
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
                        COALESCE((SELECT il2.average_cost FROM inventory_ledger il2
                            WHERE il2.product_id = p.id AND il2.tenant_id = p.tenant_id
                              AND il2.average_cost IS NOT NULL
                            ORDER BY il2.movement_date DESC, il2.created_at DESC LIMIT 1), 0) as nilai_per_unit,
                        0::double precision as minimum_stock
                    FROM inventory_ledger il
                    WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id
                ) s ON true
                WHERE p.id = $1 AND p.tenant_id = $2
            """
            product_row = await conn.fetchrow(product_query, product_id, tenant_id)

            if not product_row:
                raise HTTPException(status_code=404, detail="Product not found")

            product = ProductStockItem(
                id=str(product_row["id"]),
                nama_produk=product_row["nama_produk"],
                satuan=product_row["satuan"],
                kategori=product_row["kategori"],
                barcode=product_row["barcode"],
                harga_jual=product_row["harga_jual"],
                stok=float(product_row["stok"]),
                nilai_per_unit=float(product_row["nilai_per_unit"])
                if product_row["nilai_per_unit"]
                else None,
                total_nilai=float(product_row["total_nilai"])
                if product_row["total_nilai"]
                else None,
                minimum_stock=float(product_row["minimum_stock"])
                if product_row["minimum_stock"]
                else None,
                is_low_stock=product_row["is_low_stock"] or False,
                lokasi_gudang=product_row["lokasi_gudang"],
                updated_at=product_row["updated_at"].isoformat()
                if product_row["updated_at"]
                else None,
            )
            nama_produk = product_row["nama_produk"]

            # 2. Get suppliers from bills (Pure Ledger)
            suppliers_query = """
                SELECT
                    b.vendor_name as nama_supplier,
                    COUNT(*) as total_purchases
                FROM bill_items bi
                JOIN bills b ON b.id = bi.bill_id
                WHERE b.tenant_id = $1
                    AND (
                        bi.product_id = $2::uuid
                        OR LOWER(bi.product_name) = LOWER($3)
                    )
                    AND b.status_v2 NOT IN ('draft', 'void')
                    AND b.vendor_name IS NOT NULL
                    AND b.vendor_name != ''
                GROUP BY b.vendor_name
                ORDER BY total_purchases DESC
            """
            supplier_rows = await conn.fetch(
                suppliers_query, tenant_id, product_id, nama_produk
            )
            suppliers = [
                SupplierItem(
                    nama_supplier=row["nama_supplier"],
                    total_purchases=row["total_purchases"],
                )
                for row in supplier_rows
            ]

            # 3. Get transaction history from inventory_ledger (Pure Ledger)
            history_query = """
                SELECT
                    il.id::text,
                    TO_CHAR(il.movement_date, 'YYYY-MM-DD') as tanggal,
                    CASE
                        WHEN il.source_type = 'BILL' THEN 'pembelian'
                        WHEN il.source_type IN ('SALES_INVOICE', 'SALE') THEN 'penjualan'
                        WHEN il.source_type = 'OPENING_BALANCE' THEN 'saldo_awal'
                        WHEN il.source_type = 'SALES_INVOICE_VOID' THEN 'void_penjualan'
                        ELSE LOWER(il.source_type)
                    END as jenis_transaksi,
                    COALESCE(il.quantity_in, 0) + COALESCE(il.quantity_out, 0) as jumlah,
                    il.unit_cost as harga_satuan,
                    il.total_cost as subtotal,
                    CASE
                        WHEN il.source_type = 'BILL' THEN (SELECT b.vendor_name FROM bills b WHERE b.id = il.source_id LIMIT 1)
                        WHEN il.source_type IN ('SALES_INVOICE', 'SALE') THEN (SELECT s.customer_name FROM sales_invoices s WHERE s.id = il.source_id LIMIT 1)
                        ELSE NULL
                    END as nama_pihak
                FROM inventory_ledger il
                WHERE il.tenant_id = $1
                    AND il.product_id = $2::uuid
                ORDER BY il.movement_date DESC, il.created_at DESC
                LIMIT 10
            """
            history_rows = await conn.fetch(history_query, tenant_id, product_id)
            transaction_history = [
                TransactionHistoryItem(
                    id=row["id"],
                    tanggal=row["tanggal"],
                    jenis_transaksi=row["jenis_transaksi"],
                    jumlah=float(row["jumlah"]),
                    satuan=product_row["satuan"],
                    harga_satuan=float(row["harga_satuan"]),
                    subtotal=float(row["subtotal"]),
                    nama_pihak=row["nama_pihak"],
                )
                for row in history_rows
            ]

            # 4. Get insight aggregates from inventory_ledger (Pure Ledger)
            insight_query = """
                SELECT
                    COALESCE(SUM(il.quantity_in), 0) as total_masuk,
                    COALESCE(SUM(il.quantity_out), 0) as total_keluar,
                    AVG(CASE WHEN il.quantity_out > 0 THEN il.quantity_out END) as rata_rata_penjualan,
                    COUNT(CASE WHEN il.quantity_out > 0 THEN 1 END) as jumlah_transaksi_penjualan
                FROM inventory_ledger il
                WHERE il.tenant_id = $1
                    AND il.product_id = $2::uuid
            """

            insight_row = await conn.fetchrow(insight_query, tenant_id, product_id)
            insight = StockInsight(
                total_masuk=float(insight_row["total_masuk"]),
                total_keluar=float(insight_row["total_keluar"]),
                rata_rata_penjualan=float(insight_row["rata_rata_penjualan"])
                if insight_row["rata_rata_penjualan"]
                else None,
                jumlah_transaksi_penjualan=insight_row["jumlah_transaksi_penjualan"],
            )

            # 5. Unit conversion - now from V007 fields in products table
            # No need to calculate from transactions anymore
            base_unit = product_row["base_unit"] or "pcs"
            wholesale_unit = product_row["wholesale_unit"]
            units_per_wholesale = product_row["units_per_wholesale"]

            # Stock is already in base unit after V008 migration
            # So stok_satuan_terkecil = stok (no multiplication needed)
            stok_satuan_terkecil = float(product_row["stok"])

            logger.info(
                f"Stock card retrieved: product={nama_produk}, tenant={tenant_id}, units_per_wholesale={units_per_wholesale}, stok={stok_satuan_terkecil} {base_unit}"
            )

            return ProductStockCardResponse(
                product=product,
                minimum_stock=float(product_row["minimum_stock"])
                if product_row["minimum_stock"]
                else None,
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
                stok_satuan_terkecil=stok_satuan_terkecil,
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get product stock card error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to fetch product stock card"
        )


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "inventory_router"}


# ========================================
# Top Products (Sales Analytics)
# ========================================


@router.get("/top-products")
async def get_top_products(
    request: Request,
    period: str = Query(
        "all", description="Period filter: all, this_month, last_month, this_year"
    ),
    limit: int = Query(10, ge=1, le=50, description="Max products to return"),
):
    """
    Top-selling products by quantity sold.
    Source: inventory_ledger outbound movements (Iron Law 16 compliant).
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Date filter (parameterized via SQL, not string interpolation)
        date_filter = ""
        if period == "this_month":
            date_filter = "AND il.created_at >= date_trunc('month', CURRENT_DATE)"
        elif period == "last_month":
            date_filter = (
                "AND il.created_at >= date_trunc('month', CURRENT_DATE - interval '1 month') "
                "AND il.created_at < date_trunc('month', CURRENT_DATE)"
            )
        elif period == "this_year":
            date_filter = "AND il.created_at >= date_trunc('year', CURRENT_DATE)"
        # "all" = no date filter

        conn = await get_db_connection()
        try:
            rows = await conn.fetch(
                f"""
                SELECT
                    il.product_id,
                    p.nama_produk AS product_name,
                    p.sku,
                    COALESCE(p.base_unit, p.satuan, 'pcs') AS unit,
                    SUM(il.quantity_out) AS total_qty_sold,
                    COUNT(DISTINCT il.source_id) AS transaction_count,
                    MIN(il.created_at) AS first_sale,
                    MAX(il.created_at) AS last_sale
                FROM inventory_ledger il
                JOIN products p ON p.id = il.product_id AND p.tenant_id = il.tenant_id
                    AND p.status = 'active' AND p.deleted_at IS NULL
                WHERE il.tenant_id = $1
                    AND il.quantity_out > 0
                    AND il.source_type IN ('SALES_INVOICE', 'POS_SALE', 'CASH_SALE', 'SALES_RECEIPT_COGS')
                    {date_filter}
                GROUP BY il.product_id, p.nama_produk, p.sku, p.base_unit, p.satuan
                ORDER BY total_qty_sold DESC
                LIMIT $2
            """,
                tenant_id,
                limit,
            )

            products = []
            for r in rows:
                products.append(
                    {
                        "product_id": str(r["product_id"]),
                        "product_name": r["product_name"] or "",
                        "sku": r["sku"] or "",
                        "unit": r["unit"],
                        "total_qty_sold": float(r["total_qty_sold"]),
                        "transaction_count": r["transaction_count"],
                        "first_sale": r["first_sale"].isoformat()
                        if r["first_sale"]
                        else None,
                        "last_sale": r["last_sale"].isoformat()
                        if r["last_sale"]
                        else None,
                    }
                )

            return {
                "success": True,
                "data": {
                    "period": period,
                    "products": products,
                    "total_products": len(products),
                },
            }
        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get top products error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch top products")


# ========================================
# Slow-Moving Products
# ========================================


@router.get("/slow-moving-products")
async def get_slow_moving_products(
    request: Request,
    period: str = Query(
        "all", description="Period filter: all, this_month, last_month, this_year"
    ),
    limit: int = Query(10, ge=1, le=50, description="Max products to return"),
):
    """
    Slow-moving products — includes products with ZERO sales.
    Source: products LEFT JOIN inventory_ledger (Iron Law 16 compliant).
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Date filter for LEFT JOIN condition (server-generated, safe)
        date_filter = ""
        if period == "this_month":
            date_filter = "AND il.created_at >= date_trunc('month', CURRENT_DATE)"
        elif period == "last_month":
            date_filter = (
                "AND il.created_at >= date_trunc('month', CURRENT_DATE - interval '1 month') "
                "AND il.created_at < date_trunc('month', CURRENT_DATE)"
            )
        elif period == "this_year":
            date_filter = "AND il.created_at >= date_trunc('year', CURRENT_DATE)"

        conn = await get_db_connection()
        try:
            rows = await conn.fetch(
                f"""
                SELECT
                    p.id AS product_id,
                    p.nama_produk AS product_name,
                    p.sku,
                    COALESCE(p.base_unit, p.satuan, 'pcs') AS unit,
                    COALESCE(SUM(il.quantity_out), 0) AS total_qty_sold,
                    COUNT(DISTINCT il.source_id) FILTER (WHERE il.source_id IS NOT NULL) AS transaction_count,
                    MAX(il.created_at) AS last_sale
                FROM products p
                LEFT JOIN inventory_ledger il
                    ON il.product_id = p.id
                    AND il.tenant_id = p.tenant_id
                    AND il.quantity_out > 0
                    AND il.source_type IN ('SALES_INVOICE', 'POS_SALE', 'CASH_SALE', 'SALES_RECEIPT_COGS')
                    {date_filter}
                WHERE p.tenant_id = $1
                    AND p.status = 'active'
                    AND p.deleted_at IS NULL
                    AND p.track_inventory = true
                GROUP BY p.id, p.nama_produk, p.sku, p.base_unit, p.satuan
                ORDER BY total_qty_sold ASC, p.nama_produk ASC
                LIMIT $2
            """,
                tenant_id,
                limit,
            )

            products = []
            for r in rows:
                products.append(
                    {
                        "product_id": str(r["product_id"]),
                        "product_name": r["product_name"] or "",
                        "sku": r["sku"] or "",
                        "unit": r["unit"],
                        "total_qty_sold": float(r["total_qty_sold"]),
                        "transaction_count": r["transaction_count"],
                        "last_sale": r["last_sale"].isoformat()
                        if r["last_sale"]
                        else None,
                    }
                )

            return {
                "success": True,
                "data": {
                    "period": period,
                    "products": products,
                    "total_products": len(products),
                },
            }
        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get slow moving products error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to fetch slow moving products"
        )


# ========================================
# Product Profit Margins
# ========================================


@router.get("/product-margins")
async def get_product_margins(
    request: Request,
    period: str = Query(
        "all", description="Period filter: all, this_month, last_month, this_year"
    ),
    limit: int = Query(10, ge=1, le=50, description="Max products to return"),
    sort: str = Query(
        "margin_desc",
        description="Sort: margin_desc, margin_asc, revenue_desc, profit_desc",
    ),
):
    """
    Product profit margins — derives actual realized margins from transaction data.
    Uses sales_invoice_items for actual revenue and unit_cost for COGS.
    Iron Law compliant: no catalog price fallbacks for margin/COGS calculations.
    Catalog sell_price and buy_price retained as reference/display fields only.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Date filter for sales subquery (server-generated, safe)
        date_filter = ""
        if period == "this_month":
            date_filter = "AND si.created_at >= date_trunc('month', CURRENT_DATE)"
        elif period == "last_month":
            date_filter = (
                "AND si.created_at >= date_trunc('month', CURRENT_DATE - interval '1 month') "
                "AND si.created_at < date_trunc('month', CURRENT_DATE)"
            )
        elif period == "this_year":
            date_filter = "AND si.created_at >= date_trunc('year', CURRENT_DATE)"

        # Sort mapping (server-generated, safe)
        sort_map = {
            "margin_desc": "margin_percent DESC, total_qty_sold DESC",
            "margin_asc": "margin_percent ASC, total_qty_sold DESC",
            "revenue_desc": "total_revenue DESC, margin_percent DESC",
            "profit_desc": "total_profit DESC, margin_percent DESC",
        }
        order_clause = sort_map.get(sort, sort_map["margin_desc"])

        conn = await get_db_connection()
        try:
            rows = await conn.fetch(
                f"""
                SELECT
                    p.id AS product_id,
                    p.nama_produk AS product_name,
                    p.sku,
                    COALESCE(p.base_unit, p.satuan, 'pcs') AS unit,
                    COALESCE(p.harga_jual, p.sales_price, 0) AS sell_price,
                    COALESCE(p.purchase_price, 0) AS buy_price,
                    -- Iron Law: derive actual realized margin from transaction data, not catalog prices
                    CASE WHEN COALESCE(sales.total_qty_sold, 0) > 0 THEN
                        (COALESCE(sales.total_revenue, 0) / sales.total_qty_sold) - (COALESCE(sales.total_cogs, 0) / sales.total_qty_sold)
                    ELSE 0 END AS unit_margin,
                    CASE WHEN COALESCE(sales.total_revenue, 0) > 0 THEN
                        ROUND(((COALESCE(sales.total_revenue, 0) - COALESCE(sales.total_cogs, 0))
                              / COALESCE(sales.total_revenue, 0) * 100)::numeric, 1)
                    ELSE 0 END AS margin_percent,
                    COALESCE(sales.total_qty_sold, 0) AS total_qty_sold,
                    COALESCE(sales.total_revenue, 0) AS total_revenue,
                    COALESCE(sales.total_cogs, 0) AS total_cogs,
                    COALESCE(sales.total_revenue, 0) - COALESCE(sales.total_cogs, 0) AS total_profit
                FROM products p
                LEFT JOIN (
                    SELECT
                        sii.item_id,
                        SUM(sii.quantity) AS total_qty_sold,
                        SUM(sii.total) AS total_revenue,
                        -- Iron Law: no fallback to catalog purchase_price; use only transaction-time unit_cost
                        SUM(sii.quantity * COALESCE(sii.unit_cost, 0)) AS total_cogs
                    FROM sales_invoice_items sii
                    JOIN sales_invoices si ON si.id = sii.invoice_id
                    WHERE si.tenant_id = $1
                        AND si.status NOT IN ('void', 'draft')
                        {date_filter}
                    GROUP BY sii.item_id
                ) sales ON sales.item_id = p.id
                WHERE p.tenant_id = $1
                    AND p.status = 'active'
                    AND p.deleted_at IS NULL
                ORDER BY {order_clause}
                LIMIT $2
            """,
                tenant_id,
                limit,
            )

            products = []
            for r in rows:
                products.append(
                    {
                        "product_id": str(r["product_id"]),
                        "product_name": r["product_name"] or "",
                        "sku": r["sku"] or "",
                        "unit": r["unit"],
                        "sell_price": float(r["sell_price"]),
                        "buy_price": float(r["buy_price"]),
                        "unit_margin": float(r["unit_margin"]),
                        "margin_percent": float(r["margin_percent"]),
                        "total_qty_sold": float(r["total_qty_sold"]),
                        "total_revenue": float(r["total_revenue"]),
                        "total_cogs": float(r["total_cogs"]),
                        "total_profit": float(r["total_profit"]),
                    }
                )

            return {
                "success": True,
                "data": {
                    "period": period,
                    "sort": sort,
                    "products": products,
                    "total_products": len(products),
                },
            }
        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get product margins error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch product margins")
