"""
Inventory Handler
Extracted from setup_orchestrator grpc_server.py for better modularity

Handles:
- inventory_query: Check stock levels
- inventory_update: Manual stock adjustments
- generate_stock_alert: Low stock warnings (NEW)
"""

import logging
import json
import grpc
from datetime import datetime

# Proto imports
import setup_orchestrator_pb2
import inventory_service_pb2

# Setup logging
logger = logging.getLogger(__name__)


class InventoryHandler:
    """
    Static class for inventory-related operations
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
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """Handle inventory stock query"""
        logger.info(f"[{trace_id}] Handling inventory_query intent")
        
        # Parse entities
        try:
            entities = json.loads(intent_response.entities_json)
            inventory_entities = entities.get("entities", {})
        except:
            inventory_entities = {}
        
        logger.info(f"[{trace_id}] Inventory entities: {inventory_entities}")
        
        # Extract product name
        product_name = inventory_entities.get("product_name") or inventory_entities.get("produk_id")
        
        if not product_name:
            milky_response = "Produk apa yang mau dicek stoknya?"
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state="awaiting_product_name",
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="clarify_product"
            )
        
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
            
            milky_response = f"Stok {product_name} ada {stok} {satuan} 📦"
            
            if stock_response.is_low_stock:
                milky_response += f"\n\n⚠️ Stok menipis! Minimum: {int(stock_response.minimum_stock)} {satuan}"
            
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            if e.code() == grpc.StatusCode.NOT_FOUND:
                milky_response = f"Produk {product_name} belum terdaftar di inventory"
            else:
                milky_response = f"Maaf, gagal cek stok. Error: {e.details()[:100]}"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            milky_response = f"Ada kendala cek stok. Error: {str(e)[:100]}"
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state="inventory_queried",
            session_id=request.session_id,
            progress_percentage=progress,
            next_action="continue"
        )
    
    @staticmethod
    async def handle_inventory_update(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
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
        product_name = inventory_entities.get("product_name") or inventory_entities.get("produk_id")
        new_quantity = inventory_entities.get("new_quantity")
        
        # Validation: product name required
        if not product_name:
            milky_response = "Produk apa yang mau diupdate stoknya?"
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state="awaiting_product_name",
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="clarify_product"
            )
        
        # Validation: new quantity required and must be >= 0
        if new_quantity is None or new_quantity < 0:
            milky_response = "Stok mau diupdate jadi berapa?"
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state="awaiting_quantity",
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="clarify_quantity"
            )
        
        # Call inventory_service.AdjustStock
        try:
            adjust_request = inventory_service_pb2.AdjustStockRequest(
                tenant_id=request.tenant_id,
                produk_id=product_name,
                lokasi_gudang="gudang-utama",
                new_quantity=float(new_quantity),
                reason=f"Manual adjustment via chat: {request.message}"
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
                
                current_state = "inventory_updated"
            else:
                milky_response = f"⚠️ Gagal update stok: {adjust_response.message}"
                current_state = "inventory_update_failed"
        
        except grpc.RpcError as e:
            logger.error(f"[{trace_id}] Inventory service error: {e}")
            milky_response = f"Maaf, ada kendala update stok. Error: {e.details()[:100]}"
            current_state = "inventory_update_error"
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}")
            milky_response = f"Ada kendala update stok. Error: {str(e)[:100]}"
            current_state = "inventory_update_error"
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state=current_state,
            session_id=request.session_id,
            progress_percentage=progress,
            next_action="continue"
        )
    
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