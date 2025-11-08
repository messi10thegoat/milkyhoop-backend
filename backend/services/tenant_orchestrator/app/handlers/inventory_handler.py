"""
Inventory Handler for Tenant Orchestrator
Adapted from setup_orchestrator for tenant mode operations

Handles:
- inventory_query: Check stock levels (READ-ONLY analytics)
- inventory_update: Manual stock adjustments
- generate_stock_alert: Low stock warnings

Key differences from setup mode:
- Returns string response instead of ProcessSetupChatResponse
- No session/progress tracking (stateless)
- Simplified error handling
"""

import logging
import json
import grpc
from datetime import datetime

# Proto imports
import inventory_service_pb2

# Setup logging
logger = logging.getLogger(__name__)


class InventoryHandler:
    """
    Static class for inventory-related operations in tenant mode
    All methods are async and work with GrpcClientManager
    """
    
    @staticmethod
    async def handle_inventory_query(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle inventory stock query
        
        CRITICAL FIX: Proper entity extraction without double nesting
        Pattern from setup_orchestrator that works:
        entities.get("entities", {}) then extract product_name
        """
        logger.info(f"[{trace_id}] Handling inventory_query intent")
        
        # Parse entities from intent_response (direct, no nesting)
        try:
            inventory_entities = json.loads(intent_response.entities_json)
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to parse entities: {e}")
            inventory_entities = {}
        
        logger.info(f"[{trace_id}] Inventory entities: {inventory_entities}")
        
        # Extract product name - Multiple fallback patterns
        product_name = (
            inventory_entities.get("product_name") or 
            inventory_entities.get("produk_id") or
            inventory_entities.get("nama_produk") or
            inventory_entities.get("product")
        )
        
        if not product_name:
            return "Maaf, produk apa yang mau dicek stoknya?"
        
        logger.info(f"[{trace_id}] Product name extracted: {product_name}")
        
        # Call inventory_service.GetStockLevel
        try:
            stock_request = inventory_service_pb2.GetStockLevelRequest(
                tenant_id=request.tenant_id,
                produk_id=product_name,
                lokasi_gudang="gudang-utama"
            )
            
            stock_response = await client_manager.stubs['inventory'].GetStockLevel(
                stock_request
            )
            
            # Format response
            stok = int(stock_response.current_stock)
            satuan = stock_response.satuan or "pcs"
            
            milky_response = f"📦 Stok {product_name}:\n\n"
            milky_response += f"   Tersedia: {stok} {satuan}\n"
            milky_response += f"   Lokasi: {stock_response.lokasi_gudang or 'Semua gudang'}\n"
            
            if stock_response.is_low_stock:
                milky_response += f"\n⚠️ Stok menipis! Minimum: {int(stock_response.minimum_stock)} {satuan}"
            
            return milky_response
            
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return f"Produk {product_name} belum terdaftar di inventory"
            else:
                return f"Maaf, gagal cek stok. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            return f"Ada kendala cek stok. Error: {str(e)[:100]}"
    
    @staticmethod
    async def handle_inventory_update(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """Handle inventory stock update via manual adjustment"""
        logger.info(f"[{trace_id}] Handling inventory_update intent")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            inventory_entities = entities.get("entities", {})
        except:
            inventory_entities = {}
        
        logger.info(f"[{trace_id}] Inventory update entities: {inventory_entities}")
        
        # Extract product name and new quantity
        product_name = (
            inventory_entities.get("product_name") or 
            inventory_entities.get("produk_id")
        )
        new_quantity = inventory_entities.get("new_quantity")
        
        # Validation: product name required
        if not product_name:
            return "Produk apa yang mau diupdate stoknya?"
        
        # Validation: new quantity required and must be >= 0
        if new_quantity is None or new_quantity < 0:
            return "Stok mau diupdate jadi berapa?"
        
        # Call inventory_service.AdjustStock
        try:
            adjust_request = inventory_service_pb2.AdjustStockRequest(
                tenant_id=request.tenant_id,
                produk_id=product_name,
                lokasi_gudang="gudang-utama",
                new_quantity=float(new_quantity),
                reason=f"Manual adjustment via tenant chat: {request.message}"
            )
            
            adjust_response = await client_manager.stubs['inventory'].AdjustStock(
                adjust_request
            )
            
            # Build success response
            if adjust_response.success:
                stok_sebelum = int(adjust_response.stok_sebelum)
                stok_setelah = int(adjust_response.stok_setelah)
                
                milky_response = f"✅ Stok {product_name} sudah diupdate "
                
                if stok_sebelum != 0:
                    milky_response += f"dari {stok_sebelum} pcs → {stok_setelah} pcs"
                else:
                    milky_response += f"jadi {stok_setelah} pcs"
                
                return milky_response
            else:
                return f"⚠️ Gagal update stok: {adjust_response.message}"
        
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            return f"Maaf, ada kendala update stok. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            return f"Ada kendala update stok. Error: {str(e)[:100]}"
    
    @staticmethod
    async def generate_stock_alert(
        stock_response,
        produk_name: str,
        trace_id: str
    ) -> str:
        """
        Generate stock alert message for low/critical stock levels
        
        Args:
            stock_response: GetStockLevelResponse from inventory_service
            produk_name: Product name for display
            trace_id: Trace ID for logging
            
        Returns:
            Alert message string (empty if no alert needed)
        """
        # Check if low stock alert
        if not stock_response.is_low_stock:
            return ""
        
        stok_sekarang = int(stock_response.current_stock)
        minimum_stock = int(stock_response.minimum_stock) if stock_response.minimum_stock else 0
        satuan = stock_response.satuan or "pcs"
        
        # Critical level: < 5 units OR < 25% of minimum
        is_critical = stok_sekarang < 5 or (minimum_stock > 0 and stok_sekarang < minimum_stock * 0.25)
        
        if is_critical:
            alert_message = f"\n\n🚨 CRITICAL: Stok {produk_name} hampir habis! "
            alert_message += f"Hanya tersisa {stok_sekarang} {satuan}!"
        else:
            alert_message = f"\n\n⚠️ Stok {produk_name} menipis! "
            alert_message += f"Tersisa {stok_sekarang} {satuan}"
            if minimum_stock > 0:
                alert_message += f" (minimum: {minimum_stock} {satuan})"
        
        logger.info(f"[{trace_id}] 📢 Stock alert generated for {produk_name}: {stok_sekarang} {satuan}")
        
        return alert_message