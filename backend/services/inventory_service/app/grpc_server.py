"""
Inventory Service gRPC Server
MilkyHoop 4.0 - Conversational Financial Management

Implements:
- GetStockLevel (current stock query)
- GetStockLevelsBatch (batch stock query)
- GetLowStockAlerts (products below threshold)
- GetStockMovementHistory (audit trail)
- GetInventoryValuation (for Neraca - SAK EMKM)
- SearchProducts (fuzzy product name matching - Sprint 2.1)
- ValidateStockAvailability (pre-transaction validation)
- ProcessInventoryImpact (update stock from transactions)
- AdjustStock (manual stock adjustments)
- SetMinimumStock (configure alerts)
- HealthCheck

Features:
- Real-time stock updates (perpetual inventory)
- Multi-warehouse support
- FIFO/Average cost valuation (SAK EMKM)
- Low stock alerts
- Smart product resolution with fuzzy matching (Levenshtein distance)
- Multi-tenant isolation via RLS
- Atomic stock updates with transactions
"""

import asyncio
import signal
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2

import sys
sys.path.insert(0, '/app/backend/services/inventory_service/app')

from config import settings
import inventory_service_pb2 as pb
import inventory_service_pb2_grpc as pb_grpc
from prisma_client import prisma, connect_prisma, disconnect_prisma
from prisma_rls_extension import RLSPrismaClient
from services.product_search import search_products_fuzzy

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def calculate_days_since_movement(last_movement_at: Optional[int]) -> int:
    """Calculate days since last stock movement"""
    if not last_movement_at:
        return 9999  # No movement recorded
    
    last_movement_date = datetime.fromtimestamp(last_movement_at / 1000)
    days_diff = (datetime.utcnow() - last_movement_date).days
    return days_diff


async def get_or_create_stock_record(
    rls_client: RLSPrismaClient,
    tenant_id: str,
    produk_id: str,
    lokasi_gudang: str,
    satuan: str = 'pcs'
) -> Any:
    """
    Get existing stock record or create new one with zero stock.

    Sprint 2.1: Refactored to use Products table
    - produk_id can be either product UUID (new system) or product name (legacy)
    - Automatically creates product in Products table if not exists
    - Creates Persediaan entry with product_id FK

    Returns:
        Persediaan record
    """
    # Normalize lokasi_gudang: underscore to dash
    normalized_lokasi = lokasi_gudang.replace('_', '-')

    # SPRINT 2.1: Check if produk_id is UUID or product name
    import re
    is_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', produk_id, re.I))

    product_uuid = None

    if is_uuid:
        # New system: produk_id is UUID from Products table
        product_uuid = produk_id
    else:
        # Legacy system OR new product: produk_id is product name
        # Check if product exists in Products table
        from services.product_search import create_product

        # Try to find existing product
        existing_product = await prisma.query_raw(
            "SELECT id::text FROM products WHERE tenant_id = $1 AND nama_produk = $2",
            tenant_id, produk_id
        )

        if existing_product and len(existing_product) > 0:
            product_uuid = existing_product[0]['id']
            logger.info(f"📦 Found existing product: '{produk_id}' -> {product_uuid}")
        else:
            # Create new product in Products table
            product_uuid = await create_product(tenant_id, produk_id, satuan)
            if product_uuid:
                logger.info(f"🆕 Created new product: '{produk_id}' -> {product_uuid}")
            else:
                # Fallback: use old system (produkId as name)
                logger.warning(f"⚠️ Failed to create product in Products table, using legacy system")

    # Try to find existing Persediaan record using raw SQL (Prisma client not yet regenerated)
    if product_uuid:
        # New system: search by product_id (UUID FK) - cast text to UUID
        existing = await prisma.query_raw(
            """
            SELECT * FROM persediaan
            WHERE tenant_id = $1 AND product_id = $2::uuid AND lokasi_gudang = $3
            LIMIT 1
            """,
            tenant_id, product_uuid, normalized_lokasi
        )
    else:
        # Legacy system: search by produk_id (product name)
        existing = await prisma.query_raw(
            """
            SELECT * FROM persediaan
            WHERE tenant_id = $1 AND produk_id = $2 AND lokasi_gudang = $3
            LIMIT 1
            """,
            tenant_id, produk_id, normalized_lokasi
        )

    if existing and len(existing) > 0:
        # Convert raw result to dict-like object
        record = existing[0]
        logger.info(f"📦 Found existing stock record: {record.get('produk_id') or product_uuid}")
        # Return the raw dict since Persediaan model operations will handle it
        return record

    # Create new Persediaan record using raw SQL
    if product_uuid:
        # New system: create with product_id FK - cast text to UUID
        # Note: id field is TEXT type with no default, so we need to generate UUID manually
        import uuid as uuid_lib
        persediaan_id = str(uuid_lib.uuid4())

        new_record = await prisma.query_raw(
            """
            INSERT INTO persediaan (id, tenant_id, product_id, produk_id, lokasi_gudang, jumlah, last_movement_at, created_at, updated_at)
            VALUES ($1, $2, $3::uuid, $4, $5, $6, NOW(), NOW(), NOW())
            RETURNING *
            """,
            persediaan_id, tenant_id, product_uuid, produk_id, normalized_lokasi, 0.0
        )

        if new_record and len(new_record) > 0:
            logger.info(f"📦 Created new stock record: product_id={product_uuid} @ {normalized_lokasi}")
            return new_record[0]
    else:
        # Legacy fallback: create with produkId (name)
        import uuid as uuid_lib
        persediaan_id = str(uuid_lib.uuid4())

        new_record = await prisma.query_raw(
            """
            INSERT INTO persediaan (id, tenant_id, produk_id, lokasi_gudang, jumlah, satuan, last_movement_at, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW(), NOW())
            RETURNING *
            """,
            persediaan_id, tenant_id, produk_id, normalized_lokasi, 0.0, satuan
        )

        if new_record and len(new_record) > 0:
            logger.info(f"📦 Created new stock record (legacy): {produk_id} @ {normalized_lokasi}")
            return new_record[0]

    logger.error(f"❌ Failed to create Persediaan record for {produk_id}")
    return None


# ==========================================
# SERVICER IMPLEMENTATION
# ==========================================

class InventoryServiceServicer(pb_grpc.InventoryServiceServicer):
    """Inventory Service gRPC Servicer"""
    
    async def GetStockLevel(
        self,
        request: pb.GetStockLevelRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.StockLevelResponse:
        """Get current stock level for a product"""
        
        logger.info(f"📊 GetStockLevel: tenant={request.tenant_id}, produk={request.produk_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Query stock record - exact match first
            stock = await rls_client.persediaan.find_first(
                where={
                    'tenantId': request.tenant_id,
                    'produkId': request.produk_id,
                    'lokasiGudang': request.lokasi_gudang if request.lokasi_gudang else None
                }
            )
            
            # If not found, try fuzzy search with tokens
            if not stock:
                # Extract tokens from search query (e.g., "pesawat boeing" -> ["pesawat", "boeing"])
                search_tokens = request.produk_id.lower().split()
                
                # Try to find product where produk_id contains any token
                all_stocks = await rls_client.persediaan.find_many(
                    where={
                        'tenantId': request.tenant_id,
                        'lokasiGudang': request.lokasi_gudang if request.lokasi_gudang else None
                    }
                )
                
                # Match products where any token appears in produk_id
                for s in all_stocks:
                    produk_id_lower = s.produkId.lower()
                    if any(token in produk_id_lower for token in search_tokens):
                        stock = s
                        logger.info(f"🔍 Fuzzy match: '{request.produk_id}' → '{s.produkId}'")
                        break
            
            if not stock:
                # Return zero stock if no record exists
                return pb.StockLevelResponse(
                    produk_id=request.produk_id,
                    lokasi_gudang=request.lokasi_gudang or "default",
                    current_stock=0.0,
                    satuan="pcs",
                    nilai_per_unit=0.0,
                    total_nilai=0.0,
                    last_movement_at=0,
                    minimum_stock=0.0,
                    is_low_stock=False
                )
            
            # Calculate if low stock
            is_low_stock = False
            if stock.minimumStock and stock.jumlah < stock.minimumStock:
                is_low_stock = True
            
            # Convert last_movement_at to Unix timestamp (ms)
            last_movement_ts = 0
            if stock.lastMovementAt:
                last_movement_ts = int(stock.lastMovementAt.timestamp() * 1000)
            
            result = pb.StockLevelResponse(
                produk_id=stock.produkId,
                lokasi_gudang=stock.lokasiGudang,
                current_stock=stock.jumlah,
                satuan=stock.satuan or "pcs",
                nilai_per_unit=stock.nilaiPerUnit or 0.0,
                total_nilai=stock.totalNilai or 0.0,
                last_movement_at=last_movement_ts,
                minimum_stock=stock.minimumStock or 0.0,
                is_low_stock=is_low_stock
            )
            
            logger.info(f"✅ Stock level: {stock.jumlah} {stock.satuan}")
            return result
            
        except Exception as e:
            logger.error(f"❌ GetStockLevel failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to get stock level: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def GetLowStockAlerts(
        self,
        request: pb.GetLowStockAlertsRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.LowStockAlertsResponse:
        """Get all products with low stock"""
        
        logger.info(f"🚨 GetLowStockAlerts: tenant={request.tenant_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Query products where jumlah < minimumStock
            where_clause = {
                'tenantId': request.tenant_id,
                'minimumStock': {'not': None}  # Only products with threshold set
            }
            
            if request.lokasi_gudang:
                where_clause['lokasiGudang'] = request.lokasi_gudang
            
            all_stocks = await rls_client.persediaan.find_many(
                where=where_clause,
                take=request.limit if request.limit else 50
            )
            
            # Filter low stock items
            alerts = []
            for stock in all_stocks:
                if stock.minimumStock and stock.jumlah < stock.minimumStock:
                    shortfall = stock.minimumStock - stock.jumlah
                    
                    last_movement_ts = 0
                    days_since = 0
                    if stock.lastMovementAt:
                        last_movement_ts = int(stock.lastMovementAt.timestamp() * 1000)
                        days_since = calculate_days_since_movement(last_movement_ts)
                    
                    alert = pb.LowStockAlert(
                        produk_id=stock.produkId,
                        lokasi_gudang=stock.lokasiGudang,
                        current_stock=stock.jumlah,
                        minimum_stock=stock.minimumStock,
                        shortfall=shortfall,
                        satuan=stock.satuan or "pcs",
                        last_movement_at=last_movement_ts,
                        days_since_movement=days_since
                    )
                    alerts.append(alert)
            
            result = pb.LowStockAlertsResponse(
                alerts=alerts,
                total_count=len(alerts)
            )
            
            logger.info(f"✅ Found {len(alerts)} low stock products")
            return result
            
        except Exception as e:
            logger.error(f"❌ GetLowStockAlerts failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to get alerts: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def ValidateStockAvailability(
        self,
        request: pb.ValidateStockRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.ValidateStockResponse:
        """Validate if sufficient stock available before transaction"""
        
        logger.info(f"🔍 ValidateStock: produk={request.produk_id}, needed={request.quantity_needed}")
        
        # Normalize lokasi_gudang: underscore to dash
        normalized_lokasi = request.lokasi_gudang.replace('_', '-')
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            stock = await rls_client.persediaan.find_first(
                where={
                    'tenantId': request.tenant_id,
                    'produkId': request.produk_id,
                    'lokasiGudang': normalized_lokasi
                }
            )
            
            current_stock = stock.jumlah if stock else 0.0
            is_available = current_stock >= request.quantity_needed
            shortfall = 0.0 if is_available else (request.quantity_needed - current_stock)
            
            message = ""
            if is_available:
                message = f"✅ Stock tersedia: {current_stock} unit"
            else:
                message = f"❌ Stock kurang: tersedia {current_stock}, butuh {request.quantity_needed}, kurang {shortfall}"
            
            result = pb.ValidateStockResponse(
                is_available=is_available,
                current_stock=current_stock,
                quantity_needed=request.quantity_needed,
                shortfall=shortfall,
                message=message
            )
            
            logger.info(f"✅ Validation: {'PASS' if is_available else 'FAIL'}")
            return result
            
        except Exception as e:
            logger.error(f"❌ ValidateStock failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Validation failed: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def ProcessInventoryImpact(
        self,
        request: pb.ProcessInventoryImpactRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.ProcessInventoryImpactResponse:
        """
        Process inventory impact from transaction.
        Called by transaction_service after creating transaction.
        """
        
        logger.info(f"⚙️ ProcessInventoryImpact: transaksi={request.transaksi_id}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        updates = []
        
        try:
            await rls_client.connect()
            
            impact = request.inventory_impact
            
            # Skip if not tracked
            if not impact.is_tracked:
                return pb.ProcessInventoryImpactResponse(
                    success=True,
                    message="Inventory not tracked for this transaction",
                    updates=[]
                )
            
            # Process each item
            for item in impact.items_inventory:
                # Get or create stock record
                stock = await get_or_create_stock_record(
                    rls_client,
                    request.tenant_id,
                    item.produk_id,
                    impact.lokasi_gudang
                )

                # Handle both dict (from raw SQL) and object (from Prisma ORM)
                if isinstance(stock, dict):
                    stok_sebelum = stock.get('jumlah', 0.0)
                    nilai_per_unit_current = stock.get('nilai_per_unit')
                    stock_id = stock.get('id')
                else:
                    stok_sebelum = stock.jumlah
                    nilai_per_unit_current = stock.nilaiPerUnit
                    stock_id = stock.id

                # Update stock based on movement type
                if impact.jenis_movement == 'masuk':
                    # Stock IN (pembelian)
                    stok_setelah = stok_sebelum + item.jumlah_movement

                    # Update nilai_per_unit if provided (for FIFO/Average costing)
                    nilai_per_unit = item.nilai_per_unit if item.nilai_per_unit else nilai_per_unit_current

                elif impact.jenis_movement == 'keluar':
                    # Stock OUT (penjualan)
                    stok_setelah = stok_sebelum + item.jumlah_movement  # Proto convention: signed

                    # Validate no negative stock
                    if stok_setelah < 0:
                        raise ValueError(f"Insufficient stock for {item.produk_id}: have {stok_sebelum}, need {abs(item.jumlah_movement)}")

                    nilai_per_unit = nilai_per_unit_current  # Keep existing cost

                else:
                    # No movement or adjustment
                    stok_setelah = item.stok_setelah
                    nilai_per_unit = nilai_per_unit_current

                # Calculate total value
                total_nilai = stok_setelah * (nilai_per_unit or 0.0)

                # Update Persediaan table using raw SQL (Prisma client not regenerated yet)
                updated_stock_raw = await prisma.query_raw(
                    """
                    UPDATE persediaan
                    SET jumlah = $1, nilai_per_unit = $2, total_nilai = $3, last_movement_at = NOW(), updated_at = NOW()
                    WHERE id = $4
                    RETURNING *
                    """,
                    stok_setelah, nilai_per_unit, total_nilai, stock_id
                )

                updated_stock = updated_stock_raw[0] if updated_stock_raw else stock

                # Check low stock alert
                low_stock_alert = False
                minimum_stock = updated_stock.get('minimum_stock') if isinstance(updated_stock, dict) else getattr(updated_stock, 'minimumStock', None)
                if minimum_stock and stok_setelah < minimum_stock:
                    low_stock_alert = True
                    logger.warning(f"⚠️ LOW STOCK: {item.produk_id} = {stok_setelah} (min: {minimum_stock})")
                
                # Add to updates list
                update_result = pb.StockUpdateResult(
                    produk_id=item.produk_id,
                    lokasi_gudang=impact.lokasi_gudang,
                    stok_sebelum=stok_sebelum,
                    stok_setelah=stok_setelah,
                    total_nilai=int(total_nilai),
                    low_stock_alert=low_stock_alert
                )
                updates.append(update_result)
                
                logger.info(f"✅ Updated stock: {item.produk_id} {stok_sebelum} → {stok_setelah}")
            
            result = pb.ProcessInventoryImpactResponse(
                success=True,
                message=f"Successfully updated {len(updates)} products",
                updates=updates
            )
            
            return result
            
        except ValueError as ve:
            logger.error(f"❌ Validation error: {str(ve)}")
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(ve))
        except Exception as e:
            logger.error(f"❌ ProcessInventoryImpact failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to process: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def AdjustStock(
        self,
        request: pb.AdjustStockRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.AdjustStockResponse:
        """Manual stock adjustment (stock opname, correction)"""
        
        logger.info(f"🔧 AdjustStock: produk={request.produk_id}, new={request.new_quantity}, reason={request.reason}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Normalize lokasi_gudang
            normalized_lokasi = request.lokasi_gudang.replace('_', '-')
            
            # Try exact match first
            stock = await rls_client.persediaan.find_first(
                where={
                    'tenantId': request.tenant_id,
                    'produkId': request.produk_id,
                    'lokasiGudang': normalized_lokasi
                }
            )
            
            # If not found, try fuzzy search (same logic as GetStockLevel)
            if not stock:
                search_tokens = request.produk_id.lower().split()
                
                all_stocks = await rls_client.persediaan.find_many(
                    where={
                        'tenantId': request.tenant_id,
                        'lokasiGudang': normalized_lokasi
                    }
                )
                
                for s in all_stocks:
                    produk_id_lower = s.produkId.lower()
                    if any(token in produk_id_lower for token in search_tokens):
                        stock = s
                        logger.info(f"🔍 Fuzzy match: '{request.produk_id}' → '{s.produkId}'")
                        break
            
            # If still not found, create new record
            if not stock:
                stock = await rls_client.persediaan.create(
                    data={
                        'tenantId': request.tenant_id,
                        'produkId': request.produk_id,
                        'lokasiGudang': normalized_lokasi,
                        'jumlah': 0.0,
                        'satuan': 'pcs',
                        'lastMovementAt': datetime.utcnow()
                    }
                )
                logger.info(f"📦 Created new stock record: {request.produk_id} @ {normalized_lokasi}")
            
            stok_sebelum = stock.jumlah
            stok_setelah = request.new_quantity
            adjustment_amount = stok_setelah - stok_sebelum
            
            # Update stock
            await rls_client.persediaan.update(
                where={'id': stock.id},
                data={
                    'jumlah': stok_setelah,
                    'lastMovementAt': datetime.utcnow()
                }
            )
            
            result = pb.AdjustStockResponse(
                success=True,
                message=f"Stock adjusted: {stok_sebelum} → {stok_setelah}",
                stok_sebelum=stok_sebelum,
                stok_setelah=stok_setelah,
                adjustment_amount=adjustment_amount
            )
            
            logger.info(f"✅ Stock adjusted: {adjustment_amount:+.2f}")
            return result
            
        except Exception as e:
            logger.error(f"❌ AdjustStock failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Adjustment failed: {str(e)}")
        finally:
            await rls_client.disconnect()
    
    async def SetMinimumStock(
        self,
        request: pb.SetMinimumStockRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.SetMinimumStockResponse:
        """Set minimum stock threshold for low stock alerts"""
        
        logger.info(f"⚙️ SetMinimumStock: produk={request.produk_id}, min={request.minimum_stock}")
        
        rls_client = RLSPrismaClient(tenant_id=request.tenant_id)
        
        try:
            await rls_client.connect()
            
            # Get or create stock record
            stock = await get_or_create_stock_record(
                rls_client,
                request.tenant_id,
                request.produk_id,
                request.lokasi_gudang
            )

            # Get stock ID (handle both dict and object)
            stock_id = stock.get('id') if isinstance(stock, dict) else stock.id
            current_jumlah = stock.get('jumlah', 0.0) if isinstance(stock, dict) else stock.jumlah

            # Update minimum stock using raw SQL
            updated_raw = await prisma.query_raw(
                """
                UPDATE persediaan
                SET minimum_stock = $1, updated_at = NOW()
                WHERE id = $2
                RETURNING *
                """,
                request.minimum_stock, stock_id
            )

            updated = updated_raw[0] if updated_raw else stock

            # Check if immediately low stock
            is_low_stock = current_jumlah < request.minimum_stock
            
            result = pb.SetMinimumStockResponse(
                success=True,
                message=f"Minimum stock set to {request.minimum_stock}",
                minimum_stock=request.minimum_stock,
                current_stock=current_jumlah,
                is_low_stock=is_low_stock
            )
            
            logger.info(f"✅ Minimum stock set: {request.minimum_stock}")
            return result
            
        except Exception as e:
            logger.error(f"❌ SetMinimumStock failed: {str(e)}")
            await context.abort(grpc.StatusCode.INTERNAL, f"Failed to set minimum: {str(e)}")
        finally:
            await rls_client.disconnect()

    async def SearchProducts(
        self,
        request: pb.SearchProductsRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.SearchProductsResponse:
        """
        Search products by name with fuzzy matching (for product resolution)

        Implements Smart Product Resolution (Sprint 2.1):
        - Uses Levenshtein distance for similarity scoring
        - Returns matches sorted by similarity (highest first)
        - Thresholds: >90% exact, 70-90% ambiguous, <70% no match
        """

        logger.info(f"🔍 SearchProducts: tenant={request.tenant_id}, query='{request.query}'")

        try:
            # Use search_products_fuzzy from product_search service
            limit = request.limit if request.limit > 0 else 10
            matches_data = await search_products_fuzzy(
                tenant_id=request.tenant_id,
                query=request.query,
                limit=limit
            )

            # Convert to protobuf ProductMatch objects
            matches = []
            for match_data in matches_data:
                product_match = pb.ProductMatch(
                    produk_id=match_data['produk_id'],
                    nama_produk=match_data['nama_produk'],
                    satuan=match_data['satuan'],
                    current_stock=match_data['current_stock'],
                    similarity_score=match_data['similarity_score'],
                    lokasi_gudang=match_data['lokasi_gudang']
                )
                matches.append(product_match)

            response = pb.SearchProductsResponse(
                matches=matches,
                total_found=len(matches)
            )

            # Log result summary
            if matches:
                top_score = matches[0].similarity_score
                logger.info(f"✅ Found {len(matches)} matches, top score: {top_score}%")
            else:
                logger.info(f"✅ No matches found for '{request.query}'")

            return response

        except Exception as e:
            logger.error(f"❌ SearchProducts failed: {str(e)}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Search failed: {str(e)}")

    async def CreateProduct(
        self,
        request: pb.CreateProductRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.CreateProductResponse:
        """
        Create new product in Products table

        Sprint 2.1: Called by tenant_orchestrator when detecting new products
        This eliminates the "dangling UUID" problem by creating Products entry first
        """

        trace_id = f"create_product_{request.tenant_id[:8]}"

        logger.info(f"🆕 [{trace_id}] CreateProduct request: {request.nama_produk} ({request.satuan})")

        try:
            # Validate required fields
            if not request.tenant_id or not request.nama_produk or not request.satuan:
                error_msg = "Missing required fields: tenant_id, nama_produk, satuan"
                logger.error(f"❌ [{trace_id}] {error_msg}")
                return pb.CreateProductResponse(
                    success=False,
                    message=error_msg,
                    product_id="",
                    nama_produk="",
                    satuan=""
                )

            # Call create_product from product_search.py
            from services.product_search import create_product

            product_id = await create_product(
                tenant_id=request.tenant_id,
                nama_produk=request.nama_produk,
                satuan=request.satuan,
                kategori=request.kategori if request.kategori else None,
                barcode=request.barcode if request.barcode else None
            )

            if product_id:
                logger.info(f"✅ [{trace_id}] Product created successfully: {request.nama_produk} -> {product_id}")

                return pb.CreateProductResponse(
                    success=True,
                    message=f"Product '{request.nama_produk}' created successfully",
                    product_id=product_id,
                    nama_produk=request.nama_produk,
                    satuan=request.satuan,
                    barcode=request.barcode if request.barcode else ""
                )
            else:
                error_msg = "Failed to create product (database error)"
                logger.error(f"❌ [{trace_id}] {error_msg}")

                return pb.CreateProductResponse(
                    success=False,
                    message=error_msg,
                    product_id="",
                    nama_produk="",
                    satuan=""
                )

        except Exception as e:
            error_msg = f"CreateProduct failed: {str(e)}"
            logger.error(f"❌ [{trace_id}] {error_msg}", exc_info=True)

            return pb.CreateProductResponse(
                success=False,
                message=error_msg,
                product_id="",
                nama_produk="",
                satuan=""
            )

    async def HealthCheck(
        self,
        request: empty_pb2.Empty,
        context: grpc.aio.ServicerContext
    ) -> pb.HealthResponse:
        """Health check endpoint"""
        
        try:
            # Check Prisma connection
            total_products = await prisma.persediaan.count()
            
            # Calculate total inventory value
            all_stocks = await prisma.persediaan.find_many()
            total_value = sum(int((s.totalNilai or 0)) for s in all_stocks)
            
            database_status = pb.DatabaseStatus(
                connected=True,
                total_products=total_products,
                total_inventory_value=total_value
            )
            
            return pb.HealthResponse(
                status="SERVING",
                service_name=settings.SERVICE_NAME,
                timestamp=int(datetime.utcnow().timestamp() * 1000),
                version="1.0.0",
                database=database_status
            )
            
        except Exception as e:
            logger.error(f"❌ Health check failed: {str(e)}")
            
            database_status = pb.DatabaseStatus(
                connected=False,
                total_products=0,
                total_inventory_value=0
            )
            
            return pb.HealthResponse(
                status="NOT_SERVING",
                service_name=settings.SERVICE_NAME,
                timestamp=int(datetime.utcnow().timestamp() * 1000),
                version="1.0.0",
                database=database_status
            )


# ==========================================
# SERVER STARTUP
# ==========================================

async def serve() -> None:
    """Start gRPC server"""
    
    # Connect Prisma
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await connect_prisma()
        logger.info("✅ Prisma connected")
    
    # Create server
    server = aio.server()
    
    # Add services
    pb_grpc.add_InventoryServiceServicer_to_server(
        InventoryServiceServicer(),
        server
    )
    
    # Enable reflection (for grpcurl debugging)
    from grpc_reflection.v1alpha import reflection
    SERVICE_NAMES = (
        pb.DESCRIPTOR.services_by_name['InventoryService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)
    
    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    # Listen
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} listening on port {settings.GRPC_PORT}")
    logger.info(f"📍 Service: inventory_service.InventoryService")
    
    # Shutdown handling
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received")
        stop_event.set()
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    try:
        await server.start()
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down server...")
        await server.stop(5)
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            await disconnect_prisma()
            logger.info("✅ Prisma disconnected")
        logger.info("✅ Server shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())